from flask import Flask, request, jsonify, send_from_directory
import json, os, re, time, base64, tempfile, shutil, uuid, threading
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# ── 导入分析模块 ──────────────────────────────────────
from analysis import (
    hard_filter, ai_detect_category_and_aspects,
    ai_classify, aspect_analysis,
    ai_extract_keywords, ai_generate_suggestion,
    generate_wordcloud,
    FALLBACK_ASPECTS, COL_CONTENT, COL_RATING,
    COL_TIME, COL_PRODUCT, COL_LIKES,
    MAX_WORKERS
)

app = Flask(__name__, static_folder='.', static_url_path='')

# ── 全局任务状态管理 ──────────────────────────────────
analysis_jobs = {}  # {job_id: {'status': 'processing'|'completed'|'failed', 'results': [...], 'error': '...', 'progress': 0-100}}
jobs_lock = Lock()  # ✅ 问题3：并发锁防止数据竞争


# ══════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════

def img_to_b64(path):
    """图片转Base64"""
    if os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                return base64.b64encode(f.read()).decode('utf-8')
        except Exception as e:
            print(f"Base64转换失败: {e}")
    return ''


def process_analysis(job_id, reviews_list, api_key):
    """✅ 后台任务：异步处理分析（问题2修复）"""
    
    # 临时目录（问题1修复）
    temp_dir = tempfile.mkdtemp(prefix=f'analysis_{job_id}_')
    
    try:
        # 注入API Key
        if api_key:
            os.environ['ZHIPUAI_API_KEY'] = api_key

        # 转DataFrame
        df = pd.DataFrame(reviews_list)

        # 补全缺失列
        if COL_CONTENT not in df.columns and len(df.columns) == 1:
            df.columns = [COL_CONTENT]

        if COL_PRODUCT not in df.columns:
            df[COL_PRODUCT] = '未知商品'
        if COL_RATING not in df.columns:
            df[COL_RATING] = 3

        if COL_LIKES in df.columns:
            df[COL_LIKES] = pd.to_numeric(df[COL_LIKES], errors='coerce').fillna(0).astype(int)
        else:
            df[COL_LIKES] = 0

        all_results = []
        product_list = list(df.groupby(COL_PRODUCT))
        
        for product_idx, (product_name, pdf) in enumerate(product_list):
            # 更新进度
            with jobs_lock:
                analysis_jobs[job_id]['progress'] = int((product_idx / len(product_list)) * 100)
                analysis_jobs[job_id]['current_product'] = product_name

            work_df = pdf.copy().reset_index(drop=True)

            # [1] 硬过滤
            work_df['hard_label'] = work_df.apply(hard_filter, axis=1)
            hard_in = work_df[work_df['hard_label'] == '通过'].copy()

            # [2] 品类识别
            sample_reviews = hard_in[COL_CONTENT].tolist()[:15]
            ai_category_name, dynamic_aspects = ai_detect_category_and_aspects(sample_reviews)
            category_name = ai_category_name if ai_category_name else product_name

            # [3] AI软分类（并发）
            if not os.getenv('ZHIPUAI_API_KEY'):
                hard_in['ai_category'] = 'AI未开启'
            else:
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = {
                        executor.submit(
                            ai_classify,
                            row[COL_CONTENT],
                            int(row.get(COL_RATING, 3)),
                            int(row.get(COL_LIKES, 0))
                        ): idx
                        for idx, row in hard_in.iterrows()
                    }
                    results = {futures[f]: f.result() for f in as_completed(futures)}
                hard_in['ai_category'] = hard_in.index.map(results)

            work_df['ai_category'] = work_df['hard_label']
            work_df.loc[hard_in.index, 'ai_category'] = hard_in['ai_category']

            cat_counts = work_df['ai_category'].value_counts().to_dict()
            good_df = work_df[work_df['ai_category'] == '有效好评']
            bad_df = work_df[work_df['ai_category'] == '有效差评']
            fake_count = sum(v for k, v in cat_counts.items()
                            if k not in ('有效好评', '有效差评'))

            # [4] 维度分析
            valid_df = work_df[work_df['ai_category'].isin(['有效好评', '有效差评'])]
            aspect_stats = aspect_analysis(valid_df, dynamic_aspects)

            # [5] 关键词提取
            good_kw = ai_extract_keywords(
                ' '.join(good_df[COL_CONTENT].tolist()), '好评', category_name)
            bad_kw = ai_extract_keywords(
                ' '.join(bad_df[COL_CONTENT].tolist()), '差评', category_name)

            # ✅ 问题1修复：词云保存到临时目录
            good_wc_path = os.path.join(temp_dir, f'wordcloud_good_{product_idx}.png')
            bad_wc_path = os.path.join(temp_dir, f'wordcloud_bad_{product_idx}.png')
            generate_wordcloud(good_kw, good_wc_path, 'Blues')
            generate_wordcloud(bad_kw, bad_wc_path, 'YlOrRd')

            # [6] 建议生成
            suggestion = ai_generate_suggestion(
                product_name, category_name,
                good_kw, bad_kw, aspect_stats,
                total=len(work_df),
                good_count=len(good_df),
                bad_count=len(bad_df),
                fake_count=fake_count
            )

            # 关键词转字符串
            if isinstance(good_kw, dict):
                good_kw_str = "、".join(good_kw.keys()) if good_kw else ""
            else:
                good_kw_str = str(good_kw) if good_kw else ""

            if isinstance(bad_kw, dict):
                bad_kw_str = "、".join(bad_kw.keys()) if bad_kw else ""
            else:
                bad_kw_str = str(bad_kw) if bad_kw else ""

            product_result = {
                'product_name': product_name,
                'category_name': category_name,
                'total_reviews': len(work_df),
                'category_distribution': cat_counts,
                'aspect_mention_count': aspect_stats,
                'good_keywords': good_kw_str,
                'bad_keywords': bad_kw_str,
                'suggestion': suggestion,
                'good_wordcloud_base64': img_to_b64(good_wc_path),
                'bad_wordcloud_base64': img_to_b64(bad_wc_path),
            }
            all_results.append(product_result)

        # ✅ 问题3修复：保存到独立的结果文件（不是全局MERGED_JSON_PATH）
        os.makedirs('results', exist_ok=True)
        result_file = f'results/{job_id}.json'
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        # 标记任务完成
        with jobs_lock:
            analysis_jobs[job_id]['status'] = 'completed'
            analysis_jobs[job_id]['results'] = all_results
            analysis_jobs[job_id]['progress'] = 100

    except Exception as e:
        print(f"❌ 分析失败 ({job_id}): {e}")
        with jobs_lock:
            analysis_jobs[job_id]['status'] = 'failed'
            analysis_jobs[job_id]['error'] = str(e)

    finally:
        # ✅ 问题1修复：清理临时目录
        shutil.rmtree(temp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
#  API接口
# ══════════════════════════════════════════════════════════════════

@app.route('/analyze', methods=['POST'])
def analyze():
    """✅ 核心分析接口 - 支持异步处理"""
    try:
        data = request.json
        reviews_list = data.get('reviews', [])
        api_key = data.get('api_key', '')

        # 自适应：单个对象 → 数组
        if isinstance(reviews_list, dict):
            reviews_list = [reviews_list]

        if not reviews_list:
            return jsonify({'success': False, 'error': 'reviews不能为空'}), 400

        # ✅ 问题3修复：为这个请求生成独立的job_id
        job_id = str(uuid.uuid4())[:8]

        # 初始化任务
        with jobs_lock:
            analysis_jobs[job_id] = {
                'status': 'processing',
                'progress': 0,
                'current_product': '',
                'results': None,
                'error': None
            }

        # ✅ 问题2修复：后台异步处理，立即返回job_id
        thread = threading.Thread(
            target=process_analysis,
            args=(job_id, reviews_list, api_key),
            daemon=True
        )
        thread.start()

        return jsonify({
            'success': True,
            'job_id': job_id,
            'status': 'processing',
            'message': '分析任务已启动，请轮询查询结果'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/analyze/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """✅ 获取分析进度"""
    with jobs_lock:
        if job_id not in analysis_jobs:
            return jsonify({'success': False, 'error': '任务不存在'}), 404

        job_info = analysis_jobs[job_id]

    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': job_info['status'],  # 'processing' | 'completed' | 'failed'
        'progress': job_info['progress'],
        'current_product': job_info['current_product'],
        'error': job_info['error']
    })


@app.route('/analyze/result/<job_id>', methods=['GET'])
def get_result(job_id):
    """✅ 获取分析结果"""
    with jobs_lock:
        if job_id not in analysis_jobs:
            return jsonify({'success': False, 'error': '任务不存在'}), 404

        job_info = analysis_jobs[job_id]

    if job_info['status'] == 'processing':
        return jsonify({
            'success': False,
            'status': 'processing',
            'progress': job_info['progress'],
            'message': '分析进行中，请稍候'
        }), 202  # 202 Accepted

    if job_info['status'] == 'failed':
        return jsonify({
            'success': False,
            'status': 'failed',
            'error': job_info['error']
        }), 400

    # 已完成
    return jsonify({
        'success': True,
        'results': job_info['results']
    })


@app.route('/')
@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'dashboard.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': '言之有品·评论分析API'})


@app.route('/api/result')
def get_legacy_result():
    """兼容旧接口（不推荐）"""
    try:
        # 获取最新的结果文件
        results_dir = 'results'
        if os.path.exists(results_dir):
            files = sorted(os.listdir(results_dir))
            if files:
                with open(os.path.join(results_dir, files[-1]), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return jsonify({'success': True, 'results': data})

        return jsonify({
            'success': False,
            'results': [],
            'message': '暂无分析数据'
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
