# -*- encoding: utf-8 -*-
import os
import json
import pandas as pd
import numpy as np
import jieba
import warnings
from collections import Counter
from wordcloud import WordCloud
import matplotlib
matplotlib.use('Agg')  # 使用非图形化后端
import matplotlib.pyplot as plt
from datetime import datetime
import zhipuai

warnings.filterwarnings('ignore')

# 配置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# 模型配置
MODEL = "glm-4-flash"
API_KEY = os.getenv('ZHIPUAI_API_KEY')

if not API_KEY:
    raise ValueError("ZHIPUAI_API_KEY 环境变量未设置")

client = zhipuai.ZhipuAI(api_key=API_KEY)

# 停用词表
STOPWORDS = {
    '的', '了', '和', '是', '在', '有', '等', '把', '被', '那', '这',
    '个', '一', '两', '三', '四', '五', '六', '七', '八', '九', '十',
    '我', '你', '他', '她', '它', '们', '很', '非常', '太', '真', '也',
    '还', '又', '再', '就', '才', '所以', '因为', '但是', '然而', '所以',
    '到', '从', '给', '给我', '给了', '有点', '有些', '还有', '没有',
    '京东', '淘宝', '商品', '产品', '东西', '包装', '发货', '收到', '好的',
    '不错', '还是', '挺好', '感觉', '比较', '确实', '这样', '就是', '现在'
}

# 评价维度关键词映射
DIMENSION_KEYWORDS = {
    '质量': ['质量', '做工', '材料', '工艺', '耐用', '坚固', '结实'],
    '性能': ['性能', '速度', '流畅', '快速', '反应', '卡顿', '延迟'],
    '外观': ['外观', '颜色', '设计', '造型', '好看', '漂亮', '美观'],
    '价格': ['价格', '贵', '便宜', '划算', '性价比', '值', '贵了'],
    '屏幕': ['屏幕', '显示', '清晰', '亮度', '分辨率', '刷新'],
    '电池': ['电池', '续航', '待机', '发热', '充电', '耗电'],
    '物流': ['物流', '快递', '配送', '送达', '包装', '破损'],
    '服务': ['服务', '售后', '退货', '保修', '客服', '态度'],
}


def call_glm(prompt, temperature=0.3):
    """调用智谱 API"""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"API 调用失败: {e}")
        return ""


def extract_product_and_dimensions(reviews_text):
    """提取产品名称和评价维度"""
    prompt = f"""分析以下评论文本，提取：
1. 产品的主要类别（如：手机、电脑、衣服等）
2. 这个产品的主要评价维度（5-10个，如：质量、价格、性能等）

评论文本：
{reviews_text[:1000]}

返回 JSON 格式（只返回 JSON，不要其他内容）：
{{
    "category": "产品类别",
    "dimensions": ["维度1", "维度2", ...]
}}"""
    
    result = call_glm(prompt, temperature=0.3)
    try:
        return json.loads(result)
    except:
        return {
            "category": "产品",
            "dimensions": ["质量", "性能", "价格", "物流", "服务"]
        }


def classify_review(content, category, dimensions):
    """分类单条评论"""
    prompt = f"""分析这条{category}评论，进行分类：

评论内容：{content}
产品类别：{category}

请判断：
1. 评论类型（有效好评/有效差评/刷评/恶意评价）
2. 评论涉及的维度（从以下选择）：{', '.join(dimensions)}
3. 情感值（0-100，0最负面，100最正面）

返回 JSON 格式（只返回 JSON，不要其他内容）：
{{
    "type": "有效好评/有效差评/刷评/恶意评价",
    "dimensions": ["维度1", "维度2"],
    "sentiment": 85
}}"""
    
    result = call_glm(prompt, temperature=0.3)
    try:
        return json.loads(result)
    except:
        return {
            "type": "有效评价",
            "dimensions": dimensions[:2],
            "sentiment": 50
        }


def extract_keywords(text):
    """提取关键词"""
    if pd.isna(text) or text == '':
        return []
    
    # 分词
    words = jieba.cut(text)
    # 过滤停用词和长度小于2的词
    keywords = [w for w in words if w not in STOPWORDS and len(w) >= 2]
    return keywords


def analyze_data(df):
    """
    主分析函数
    参数: df - pandas DataFrame，包含 review_content, rating 等列
    返回: 分析结果的列表
    """
    
    # 数据清洗
    df = df.dropna(subset=['review_content'])
    df = df[df['review_content'].astype(str).str.strip() != '']
    df['rating'] = pd.to_numeric(df['rating'], errors='coerce')
    df = df.dropna(subset=['rating'])
    
    if len(df) == 0:
        return []
    
    # 按产品分组
    results = []
    
    for product_name in df['product_model'].unique():
        product_df = df[df['product_model'] == product_name].copy()
        
        if len(product_df) == 0:
            continue
        
        # 提取维度
        all_reviews_text = ' '.join(product_df['review_content'].astype(str).head(20).tolist())
        dim_info = extract_product_and_dimensions(all_reviews_text)
        category = dim_info.get('category', '产品')
        dimensions = dim_info.get('dimensions', ['质量', '性能', '价格', '物流', '服务'])
        
        # 分类评论
        product_df['classification'] = product_df['review_content'].apply(
            lambda x: classify_review(x, category, dimensions)
        )
        
        # 解析分类结果
        product_df['review_type'] = product_df['classification'].apply(lambda x: x.get('type', '有效评价'))
        product_df['mentioned_dimensions'] = product_df['classification'].apply(lambda x: x.get('dimensions', []))
        product_df['sentiment'] = product_df['classification'].apply(lambda x: x.get('sentiment', 50))
        
        # 分离好评和差评
        good_reviews = product_df[product_df['rating'] >= 4]
        bad_reviews = product_df[product_df['rating'] <= 2]
        
        # 提取关键词
        good_words = []
        for comment in good_reviews['review_content']:
            good_words.extend(extract_keywords(comment))
        
        bad_words = []
        for comment in bad_reviews['review_content']:
            bad_words.extend(extract_keywords(comment))
        
        # 统计词频
        good_word_freq = Counter(good_words).most_common(20)
        bad_word_freq = Counter(bad_words).most_common(20)
        
        good_keywords = [w[0] for w in good_word_freq]
        bad_keywords = [w[0] for w in bad_word_freq]
        
        # 生成词云 Base64 编码
        good_wordcloud_base64 = generate_wordcloud_base64(good_words, '好评词云')
        bad_wordcloud_base64 = generate_wordcloud_base64(bad_words, '差评词云')
        
        # 维度统计
        all_dimensions = []
        for dim_list in product_df['mentioned_dimensions']:
            all_dimensions.extend(dim_list)
        
        dimension_count = Counter(all_dimensions)
        aspect_mention_count = {dim: dimension_count.get(dim, 0) for dim in dimensions}
        
        # 分类统计
        review_type_count = product_df['review_type'].value_counts().to_dict()
        
        # 生成建议
        suggestion_prompt = f"""基于以下电商评论分析数据，为商家生成改进建议：

产品：{product_name}（{category}）
总评论数：{len(product_df)}
好评占比：{len(good_reviews)/len(product_df)*100:.1f}%
差评占比：{len(bad_reviews)/len(product_df)*100:.1f}%

好评关键词：{', '.join(good_keywords)}
差评关键词：{', '.join(bad_keywords)}

主要评价维度及提及次数：
{json.dumps(aspect_mention_count, ensure_ascii=False, indent=2)}

请生成 3-5 条具体的改进建议（使用中文）。"""
        
        suggestion = call_glm(suggestion_prompt, temperature=0.6)
        
        # 构建结果
        result = {
            'product_name': product_name,
            'category_name': category,
            'total_reviews': len(product_df),
            'good_reviews': len(good_reviews),
            'bad_reviews': len(bad_reviews),
            'average_rating': round(product_df['rating'].mean(), 2),
            'review_type_distribution': review_type_count,
            'good_keywords': good_keywords[:10],
            'bad_keywords': bad_keywords[:10],
            'good_wordcloud_base64': good_wordcloud_base64,
            'bad_wordcloud_base64': bad_wordcloud_base64,
            'aspect_mention_count': aspect_mention_count,
            'suggestion': suggestion
        }
        
        results.append(result)
    
    return results


def generate_wordcloud_base64(words, title):
    """生成词云的 Base64 编码"""
    if not words:
        return ""
    
    try:
        text = ' '.join(words)
        
        plt.figure(figsize=(10, 6))
        wc = WordCloud(
            width=1000,
            height=600,
            background_color='white',
            font_path='SimHei',
            max_words=100,
            relative_scaling=0.5,
            min_font_size=10
        ).generate(text)
        
        plt.imshow(wc, interpolation='bilinear')
        plt.axis('off')
        plt.title(title, fontsize=16)
        plt.tight_layout()
        
        # 转换为 Base64
        import io
        import base64
        
        buf = io.BytesIO()
        plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close()
        
        return f"data:image/png;base64,{image_base64}"
    
    except Exception as e:
        print(f"词云生成失败: {e}")
        return ""


def main():
    """本地运行时的入口"""
    # 读取 CSV 文件
    csv_file = 'CSV_数据文件.csv'
    
    if not os.path.exists(csv_file):
        print(f"错误: {csv_file} 文件不存在")
        return
    
    df = pd.read_csv(csv_file, encoding='utf-8')
    
    # 执行分析
    results = analyze_data(df)
    
    # 保存 JSON 结果
    output_file = '结构化分析结果.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 分析完成！结果已保存到 {output_file}")
    
    # 生成仪表板（可选）
    # generate_dashboard(results)


if __name__ == "__main__":
    main()
