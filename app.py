假如你是计算机大佬并且熟练使用扣子。我现在在扣子上建了一个插件，是输入评论然后对评论进行分析，最终生成可视化大屏。这个是通过GitHub上建了一个仓库，然后在Render上进行部署，最终以post请求建立了插件。

app.py文件如下
# app.py - 把你的 analysis.py 包装成Web接口
# 你的 analysis.py 一行不用改

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

# ✅ 【新增】安全转换关键词函数 ──────────────────────────
def safe_keywords_to_string(keywords_dict):
    """
    安全地将任意类型的关键词转换为中文逗号分隔的字符串
    
    处理情况：
    - dict: {"词": 权重} → "词1、词2、词3"
    - list/tuple: ["词1", "词2"] → "词1、词2、词3"
    - None/空值: → ""
    - 其他类型: → str(value)
    """
    if not keywords_dict:
        return ""
    
    if isinstance(keywords_dict, dict):
        # 字典：取所有 key（value是权重）
        return "、".join(str(k) for k in keywords_dict.keys())
    
    if isinstance(keywords_dict, (list, tuple)):
        # 列表或元组：直接连接
        return "、".join(str(k) for k in keywords_dict)
    
    # 其他类型：强制转换
    return str(keywords_dict) if keywords_dict else ""


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

        # ✅ 核心修复：兼容单个对象 和 对象数组 两种情况
        if isinstance(reviews_list, dict):
            reviews_list = [reviews_list]

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
                ' '.join(good_df[COL_CONTENT].tolist()), '好评', category_name)
            bad_kw  = ai_extract_keywords(
                ' '.join(bad_df[COL_CONTENT].tolist()),  '差评', category_name)

            # ✅ 类型检查和转换（修复关键词格式问题）
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
