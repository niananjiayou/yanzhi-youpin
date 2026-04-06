from flask import Flask, request, jsonify, send_from_directory
import json, os, re, time, io, base64
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as req  # ✅ 新增

# ── 直接从你的 analysis.py 导入所有函数 ──────────────
from analysis import (
    hard_filter, ai_detect_category_and_aspects,
    ai_classify, aspect_analysis,
    ai_extract_keywords, ai_generate_suggestion,
    generate_wordcloud,
    FALLBACK_ASPECTS, COL_CONTENT, COL_RATING,
    COL_TIME, COL_PRODUCT, COL_LIKES,
    MAX_WORKERS, OUTPUT_DIR, MERGED_JSON_PATH
)

app = Flask(__name__, static_folder='.', static_url_path='')

# ── 接口1：分析评论（核心接口）─────────────────────────
@app.route('/analyze', methods=['POST'])
def analyze():
    try:
        data = request.json
        reviews_list = []
        api_key = ''
        
        print("=" * 50)
        print("📥 接收到请求")
        print(f"Request data keys: {list(data.keys())}")
        print(f"reviews: {data.get('reviews')}")
        print(f"review_file: {data.get('review_file')}")
        
        # ✅ 【新增】处理扣子传来的文件 URL
        if data.get('review_file') and (not data.get('reviews') or data.get('reviews') == []):
            file_url = data.get('review_file')
            
            print(f"\n📂 检测到文件 URL，开始下载...")
            print(f"URL: {file_url[:80]}...")
            
            try:
                # 下载 CSV 文件
                print("⏳ 正在下载文件...")
                response = req.get(file_url, timeout=30)
                response.raise_for_status()
                
                print(f"✅ 下载成功，文件大小: {len(response.text)} 字节")
                
                # 解析 CSV 内容
                print("⏳ 正在解析 CSV...")
                df = pd.read_csv(io.StringIO(response.text))
                print(f"✅ 解析成功，列名: {list(df.columns)}")
                print(f"✅ 共 {len(df)} 行数据")
                
                reviews_list = df.to_dict('records')
                print(f"✅ 转换为列表成功，共 {len(reviews_list)} 条")
                
                # 打印第一条数据用于调试
                if reviews_list:
                    print(f"📌 第一条数据: {reviews_list[0]}")
                
            except Exception as e:
                print(f"\n❌ 错误: {str(e)}")
                import traceback
                traceback.print_exc()
                return jsonify({
                    'success': False,
                    'error': f'❌ 解析文件 URL 失败：{str(e)}'
                }), 400
        
        # ✅ 原有的逻辑：兼容 'reviews' 和 'review' 两个参数名
        else:
            print("\n📝 使用粘贴的 JSON 数据")
            reviews_list = data.get('reviews') or data.get('review', [])
            api_key = data.get('api_key', '')
        
        # 注入 API Key
        if api_key:
            os.environ['ZHIPUAI_API_KEY'] = api_key
            print(f"✅ 已注入 API Key")

        # ✅ 核心修复：兼容单个对象 和 对象数组 两种情况
        if isinstance(reviews_list, dict):
            reviews_list = [reviews_list]

        # 确保是列表
        if not isinstance(reviews_list, list):
            print(f"❌ reviews_list 不是列表，类型: {type(reviews_list)}")
            return jsonify({'success': False, 'error': '❌ reviews 必须是数组或对象'}), 400

        print(f"\n📊 数据检查")
        print(f"reviews_list 类型: {type(reviews_list)}")
        print(f"reviews_list 长度: {len(reviews_list)}")
        
        if not reviews_list:
            print("❌ reviews_list 为空!")
            return jsonify({'success': False, 'error': '❌ 数据不能为空'}), 400

        print(f"✅ 数据验证通过，开始分析...")

        # ✅ 转DataFrame，并自动补全缺失字段
        df = pd.DataFrame(reviews_list)

        # 如果传入的是纯字符串列表，自动把唯一列设为 content
        if COL_CONTENT not in df.columns and len(df.columns) == 1:
            df.columns = [COL_CONTENT]

        # 补全缺失列
        if COL_PRODUCT not in df.columns:
            df[COL_PRODUCT] = '未知商品'
        if COL_RATING not in df.columns:
            df[COL_RATING] = 3
        if COL_LIKES in df.columns:
            df[COL_LIKES] = pd.to_numeric(df[COL_LIKES], errors='coerce').fillna(0).astype(int)
        else:
            df[COL_LIKES] = 0

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        all_results = []

        for product_name, pdf in df.groupby(COL_PRODUCT):
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', str(product_name)).strip()
            folder    = os.path.join(OUTPUT_DIR, safe_name)
            os.makedirs(folder, exist_ok=True)

            work_df = pdf.copy().reset_index(drop=True)

            # [1] 硬过滤
            work_df['hard_label'] = work_df.apply(hard_filter, axis=1)
            hard_in = work_df[work_df['hard_label'] == '通过'].copy()

            # 如果全部被过滤掉
            if len(hard_in) == 0:
                print(f"⚠️  【{product_name}】所有评论都被硬过滤，跳过分析")
                continue

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
            good_df    = work_df[work_df['ai_category'] == '有效好评']
            bad_df     = work_df[work_df['ai_category'] == '有效差评']
            fake_count = sum(v for k, v in cat_counts.items()
                            if k not in ('有效好评', '有效差评'))

            # [4] 维度分析
            valid_df     = work_df[work_df['ai_category'].isin(['有效好评', '有效差评'])]
            aspect_stats = aspect_analysis(valid_df, dynamic_aspects)

            # [5] 关键词提取
            good_kw = ai_extract_keywords(
                ' '.join(good_df[COL_CONTENT].tolist()) if len(good_df) > 0 else '', 
                '好评', category_name)
            bad_kw  = ai_extract_keywords(
                ' '.join(bad_df[COL_CONTENT].tolist()) if len(bad_df) > 0 else '', 
                '差评', category_name)

            # ✅ 类型检查和转换
            if not isinstance(good_kw, dict):
                good_kw = {}
            if not isinstance(bad_kw, dict):
                bad_kw = {}

            good_kw_str = "、".join(good_kw.keys()) if good_kw else ""
            bad_kw_str  = "、".join(bad_kw.keys())  if bad_kw  else ""
            
            # [6] 词云生成
            good_wc_path = os.path.join(folder, '词云_真实优点.png')
            bad_wc_path  = os.path.join(folder, '词云_真实缺点.png')
            generate_wordcloud(good_kw, good_wc_path, 'Blues')
            generate_wordcloud(bad_kw,  bad_wc_path,  'YlOrRd')
            
            # Base64转换函数
            def img_to_b64(path):
                if os.path.exists(path):
                    with open(path, 'rb') as f:
                        return base64.b64encode(f.read()).decode('utf-8')
                return ''

            # [7] 建议生成
            suggestion = ai_generate_suggestion(
                product_name, category_name,
                good_kw, bad_kw, aspect_stats,
                total=len(work_df),
                good_count=len(good_df),
                bad_count=len(bad_df),
                fake_count=fake_count
            )

            product_result = {
                'product_name':          product_name,
                'category_name':         category_name,
                'total_reviews':         len(work_df),
                'category_distribution': cat_counts,
                'aspect_mention_count':  aspect_stats,
                'good_keywords':         good_kw_str,
                'bad_keywords':          bad_kw_str,
                'suggestion':            suggestion,
                'good_wordcloud_base64': img_to_b64(good_wc_path),
                'bad_wordcloud_base64':  img_to_b64(bad_wc_path),
            }
            all_results.append(product_result)

        # 写入json文件
        with open(MERGED_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 分析完成！共处理 {len(all_results)} 个商品")
        print("=" * 50)

        return jsonify({
            'success': True, 
            'count': len(all_results),
            'results': all_results
        })

    except Exception as e:
        import traceback
        error_msg = traceback.format_exc()
        print(f"\n❌ 发生异常：\n{error_msg}")
        print("=" * 50)
        
        return jsonify({
            'success': False, 
            'error': str(e),
            'traceback': error_msg
        }), 500


# ── 接口2：直接访问dashboard大屏 ──────────────────────
@app.route('/')
@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'dashboard.html')


# ── 接口3：健康检查 ────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': '言之有品·评论分析API'})


# ── 接口4：dashboard读取分析结果 ──────────────────────
@app.route('/api/result')
def get_result():
    try:
        with open(MERGED_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify({'success': True, 'results': data})
    except FileNotFoundError:
        return jsonify({
            'success': False,
            'results': [],
            'message': '暂无分析数据，请先调用 /analyze 接口进行分析'
        }), 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
