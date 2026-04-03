# -*- encoding: utf-8 -*-
from flask import Flask, request, jsonify
import pandas as pd
import json
import os
from analysis import analyze_data

app = Flask(__name__)

@app.route('/analyze', methods=['POST'])
def analyze_endpoint():
    """
    接收扣子插件发送的评论数据，进行分析，返回结果
    """
    try:
        # 获取请求体中的数据
        data = request.json
        reviews = data.get('reviews', [])
        
        if not reviews:
            return jsonify({'error': '没有评论数据'}), 400
        
        # 将 reviews 列表转换为 DataFrame
        df = pd.DataFrame(reviews)
        
        # 确保列名正确
        required_columns = ['review_content', 'rating', 'review_time', 'product_model', 'likes']
        for col in required_columns:
            if col not in df.columns:
                return jsonify({'error': f'缺少必要列: {col}'}), 400
        
        # 调用分析函数
        results = analyze_data(df)
        
        # 返回 JSON 结果
        return jsonify({
            'success': True,
            'results': results
        }), 200
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/health', methods=['GET'])
def health():
    """健康检查端点"""
    return jsonify({'status': 'ok'}), 200


if __name__ == '__main__':
    # 用于本地测试
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
