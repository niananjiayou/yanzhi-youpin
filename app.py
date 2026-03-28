
# app.py - 把你的 analysis.py 包装成Web接口
# 你的 analysis.py 一行不用改！

from flask import Flask, request, jsonify, send_from_directory
import json, os, re, time, io, base64
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        data         = request.json
        reviews_list = data.get('reviews', [])
        api_key      = data.get('api_key', '')

        # 注入API Key
        if api_key:
            os.environ['ZHIPUAI_API_KEY'] = api_key

        # 转DataFrame，列名和你的代码完全一致
        df = pd.DataFrame(reviews_list)
        df[COL_LIKES] = pd.to_numeric(
            df.get(COL_LIKES, 0), errors='coerce').fillna(0).astype(int)

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

            # [2] 品类识别
            sample_reviews           = hard_in[COL_CONTENT].tolist()[:15]
            ai_category_name, dynamic_aspects = ai_detect_category_and_aspects(sample_reviews)
            category_name            = ai_category_name if ai_category_name else product_name

            # [3] AI软分类（并发，和你原来完全一样）
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
                ' '.join(good_df[COL_CONTENT].tolist()), '好评', category_name)
            bad_kw  = ai_extract_keywords(
                ' '.join(bad_df[COL_CONTENT].tolist()),  '差评', category_name)

            # [6] 词云生成（含蒙版、配色，完全用你的原函数）
            good_wc_path = os.path.join(folder, '词云_真实优点.png')
            bad_wc_path  = os.path.join(folder, '词云_真实缺点.png')
            generate_wordcloud(good_kw, good_wc_path, 'Blues')
            generate_wordcloud(bad_kw,  bad_wc_path,  'YlOrRd')

            # 词云转base64（方便扣子直接展示）
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
                'good_keywords':         good_kw,
                'bad_keywords':          bad_kw,
                'suggestion':            suggestion,
                'good_wordcloud_base64': img_to_b64(good_wc_path),
                'bad_wordcloud_base64':  img_to_b64(bad_wc_path),
            }
            all_results.append(product_result)

        # 同时写入json文件，dashboard.html可以直接读
        with open(MERGED_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)

        return jsonify({'success': True, 'results': all_results})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── 接口2：直接访问dashboard大屏 ──────────────────────
@app.route('/')
@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'dashboard.html')


# ── 接口3：健康检查 ────────────────────────────────────
@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': '言之有品·评论分析API'})


# ── 接口4：dashboard读取分析结果 ── ✅ 新增的接口 ──────
@app.route('/api/result')
def get_result():
    try:
        with open(MERGED_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return jsonify({'success': True, 'results': data})
    except FileNotFoundError:
        # 文件不存在时返回空数据，不报错
        return jsonify({
            'success': False,
            'results': [],
            'message': '暂无分析数据，请先调用 /analyze 接口进行分析'
        }), 200  # 注意返回200而不是404，避免dashboard报错


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
