from flask import Flask, request, jsonify, send_from_directory
import json, os, re, time, base64, tempfile, shutil, uuid, threading, csv
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
analysis_jobs = {}
jobs_lock = Lock()

# ✅ 问题9修复：全局API速率限制
api_call_count = 0
api_call_lock = Lock()
api_call_limit = 100
api_reset_time = time.time()


# ══════════════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════════════

def safe_keywords_to_string(keywords_dict):
    """安全转换关键词为字符串，处理各种格式"""
    if not keywords_dict:
        return ""
    
    if isinstance(keywords_dict, dict):
        return "、".join(str(k) for k in list(keywords_dict.keys())[:10])
    
    if isinstance(keywords_dict, (list, tuple)):
        return "、".join(str(k) for k in keywords_dict[:10])
    
    return str(keywords_dict)


def rate_limit_check():
    """检查今日API调用是否超限"""
    global api_call_count, api_reset_time
    
    with api_call_lock:
        current_time = time.time()
        if current_time - api_reset_time > 86400:
            api_call_count = 0
            api_reset_time = current_time
        
        if api_call_count >= api_call_limit:
            return False, f"今日API调用已达上限({api_call_limit}次)"
        
        api_call_count += 1
        return True, f"剩余: {api_call_limit - api_call_count}"


def process_analysis(job_id, reviews_list, api_key):
    """✅ 后台任务：异步处理分析"""
    
    temp_dir = tempfile.mkdtemp(prefix=f'analysis_{job_id}_')
    
    try:
        if api_key:
            os.environ['ZHIPUAI_API_KEY'] = api_key

        df = pd.DataFrame(reviews_list)

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
            render_max_workers = max(1, MAX_WORKERS - 3) if os.getenv('RENDER') else MAX_WORKERS
            
            if not os.getenv('ZHIPUAI_API_KEY'):
                hard_in['ai_category'] = 'AI未开启'
            else:
                try:
                    can_call, msg = rate_limit_check()
                    if not can_call:
                        print(f"⚠️  {msg}")
                        hard_in['ai_category'] = '配额已用完'
                    else:
                        with ThreadPoolExecutor(max_workers=render_max_workers) as executor:
                            futures = {
                                executor.submit(
                                    ai_classify,
                                    row[COL_CONTENT],
                                    int(row.get(COL_RATING, 3)),
                                    int(row.get(COL_LIKES, 0))
                                ): idx
                                for idx, row in hard_in.iterrows()
                            }
                            results = {}
                            for f in as_completed(futures, timeout=25):
                                try:
                                    results[futures[f]] = f.result(timeout=25)
                                except Exception as e:
                                    results[futures[f]] = '无效评论'
                            hard_in['ai_category'] = hard_in.index.map(results)
                except Exception as e:
                    print(f"AI分类异常: {e}")
                    hard_in['ai_category'] = '无效评论'

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
                ' '.join(good_df[COL_CONTENT].tolist())[:2000], '好评', category_name)
            bad_kw = ai_extract_keywords(
                ' '.join(bad_df[COL_CONTENT].tolist())[:2000], '差评', category_name)

            good_wc_path = os.path.join(temp_dir, f'wordcloud_good_{product_idx}.png')
            bad_wc_path = os.path.join(temp_dir, f'wordcloud_bad_{product_idx}.png')
            
            try:
                wc_thread = threading.Thread(
                    target=generate_wordcloud,
                    args=(good_kw, good_wc_path, 'Blues'),
                    daemon=False
                )
                wc_thread.start()
                wc_thread.join(timeout=8)
                
                wc_thread = threading.Thread(
                    target=generate_wordcloud,
                    args=(bad_kw, bad_wc_path, 'YlOrRd'),
                    daemon=False
                )
                wc_thread.start()
                wc_thread.join(timeout=8)
            except Exception as e:
                print(f"词云生成异常: {e}")

            # [6] 建议生成
            suggestion = ai_generate_suggestion(
                product_name, category_name,
                good_kw, bad_kw, aspect_stats,
                total=len(work_df),
                good_count=len(good_df),
                bad_count=len(bad_df),
                fake_count=fake_count
            )

            # ✅ 安全转换关键词
            good_kw_str = safe_keywords_to_string(good_kw)
            bad_kw_str = safe_keywords_to_string(bad_kw)

            # ✅ 优化3修复：词云文件保存改为URL模式
            os.makedirs('wordclouds', exist_ok=True)
            good_wc_filename = f'good_{job_id}_{product_idx}.png'
            bad_wc_filename = f'bad_{job_id}_{product_idx}.png'
            
            if os.path.exists(good_wc_path):
                shutil.copy(good_wc_path, f'wordclouds/{good_wc_filename}')
            if os.path.exists(bad_wc_path):
                shutil.copy(bad_wc_path, f'wordclouds/{bad_wc_filename}')

            product_result = {
                'product_name': product_name,
                'category_name': category_name,
                'total_reviews': len(work_df),
                'category_distribution': cat_counts,
                'aspect_mention_count': aspect_stats,
                'good_keywords': good_kw_str,
                'bad_keywords': bad_kw_str,
                'suggestion': suggestion,
                'good_wordcloud_url': f'/wordcloud/{good_wc_filename}',
                'bad_wordcloud_url': f'/wordcloud/{bad_wc_filename}',
            }
            all_results.append(product_result)

        os.makedirs('results', exist_ok=True)
        result_file = f'results/{job_id}.json'
        with open(result_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        with jobs_lock:
            analysis_jobs[job_id]['status'] = 'completed'
            analysis_jobs[job_id]['results'] = all_results
            analysis_jobs[job_id]['progress'] = 100

    except Exception as e:
        print(f"❌ 分析失败 ({job_id}): {e}")
        with jobs_lock:
            analysis_jobs[job_id]['status'] = 'failed'
            analysis_jobs[job_id]['error'] = str(e)[:200]

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
#  API接口
# ══════════════════════════════════════════════════════════════════

@app.route('/analyze', methods=['POST'])
def analyze():
    """核心分析接口 - 支持异步处理"""
    try:
        data = request.json
        reviews_list = data.get('reviews', [])
        api_key = data.get('api_key', '')

        if isinstance(reviews_list, dict):
            reviews_list = [reviews_list]

        if not reviews_list:
            return jsonify({'success': False, 'error': 'reviews不能为空'}), 400

        job_id = str(uuid.uuid4())[:8]

        with jobs_lock:
            analysis_jobs[job_id] = {
                'status': 'processing',
                'progress': 0,
                'current_product': '',
                'results': None,
                'error': None
            }

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
    """获取分析进度"""
    with jobs_lock:
        if job_id not in analysis_jobs:
            return jsonify({'success': False, 'error': '任务不存在'}), 404

        job_info = analysis_jobs[job_id]

    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': job_info['status'],
        'progress': job_info['progress'],
        'current_product': job_info['current_product'],
        'error': job_info['error']
    })


@app.route('/analyze/result/<job_id>', methods=['GET'])
def get_result(job_id):
    """获取分析结果"""
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
        }), 202

    if job_info['status'] == 'failed':
        return jsonify({
            'success': False,
            'status': 'failed',
            'error': job_info['error']
        }), 400

    return jsonify({
        'success': True,
        'results': job_info['results']
    })


@app.route('/wordcloud/<filename>')
def serve_wordcloud(filename):
    """词云文件服务 - 支持浏览器缓存和安全检查"""
    try:
        # 安全检查：防止路径穿越攻击
        if '..' in filename or '/' in filename or '\\' in filename:
            return {'error': '非法路径'}, 403
        
        # 只允许PNG文件
        if not filename.endswith('.png'):
            return {'error': '仅支持PNG文件'}, 403
        
        response = send_from_directory('wordclouds', filename)
        # 设置浏览器缓存7天
        response.cache_control.max_age = 604800
        response.cache_control.public = True
        return response
    except Exception as e:
        print(f"词云文件服务异常: {e}")
        return {'error': '文件不存在'}, 404


@app.route('/')
@app.route('/dashboard')
def dashboard():
    """大屏应用"""
    return send_from_directory('.', 'dashboard.html')


@app.route('/health')
def health():
    """健康检查端点（Render保活）"""
    return jsonify({
        'status': 'ok',
        'service': '言之有品·评论分析API',
        'api_calls_today': api_call_count,
        'timestamp': time.time()
    })

@app.route('/dashboard')
def dashboard_with_jobid():
    """✅ 根据 job_id 参数重定向或加载仪表板"""
    job_id = request.args.get('job_id')
    
    if job_id:
        # 检查任务是否存在
        with jobs_lock:
            if job_id in analysis_jobs:
                print(f"📊 加载任务: {job_id}")
                return send_from_directory('.', 'dashboard.html')
            else:
                # 任务不存在，尝试从 results 目录查找
                result_file = f'results/{job_id}.json'
                if os.path.exists(result_file):
                    return send_from_directory('.', 'dashboard.html')
        
        # 任务未找到
        return jsonify({'error': f'任务 {job_id} 不存在'}), 404
    
    # 没有 job_id，加载默认大屏
    return send_from_directory('.', 'dashboard.html')


@app.route('/')
def home():
    """主页 - 重定向到大屏"""
    return send_from_directory('.', 'dashboard.html')

@app.route('/analyze/latest')
def get_latest_result():
    """✅ 获取最新的分析结果"""
    try:
        results_dir = 'results'
        if os.path.exists(results_dir):
            files = sorted(os.listdir(results_dir))
            if files:
                # 获取最新的结果文件
                latest_file = files[-1]
                with open(os.path.join(results_dir, latest_file), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return jsonify({
                    'success': True,
                    'results': data,
                    'job_id': latest_file.replace('.json', '')
                })

        return jsonify({
            'success': False,
            'results': [],
            'message': '暂无分析数据'
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/result')
def get_legacy_result():
    """兼容旧接口"""
    return get_latest_result()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
