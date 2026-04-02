
"""
server.py - 扣子智能体的后端服务
将爬虫 + 分析插件 + 可视化 整合为 API
部署到 Render
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import sys
import json
import time
import logging
from datetime import datetime
from urllib.parse import parse_qs, urlencode

# ==================== 导入分析引擎 ====================
try:
    from analysis import analyze_reviews_main
except ImportError:
    print("⚠️ 警告：未找到 analysis.py，请确保在同一目录")
    analyze_reviews_main = None

# ==================== 初始化 Flask ====================
app = Flask(__name__)
CORS(app)

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== 爬虫函数（改自 c.py）====================

def get_jd_reviews(product_url: str, max_pages: int = 50) -> dict:
    """
    从 JD.com 爬取商品评论
    
    Args:
        product_url: JD商品链接，例如 https://item.jd.com/10127955410850.html
        max_pages: 最多爬取页数（每页20条）
    
    Returns:
        {
            "status": "success" 或 "error",
            "reviews": [评论列表],
            "count": 总数,
            "message": 消息
        }
    """
    
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        
        logger.info(f"🕷️ 开始爬取：{product_url}")
        
        # 初始化浏览器配置
        co = ChromiumOptions()
        
        # 根据系统设置浏览器路径
        if sys.platform == 'win32':
            # Windows：使用默认路径或环境变量
            chrome_path = os.environ.get('CHROME_PATH', 
                r'C:\Program Files\Google\Chrome\Application\chrome.exe')
            co.set_browser_path(chrome_path)
        else:
            # Linux/Mac：使用默认浏览器
            co.set_browser_path('/usr/bin/chromium-browser')
        
        co.set_local_port(9333)
        dp = ChromiumPage(co)
        
        # 第一步：打开页面，点"全部评价"
        dp.listen.start('client.action')
        dp.get(product_url)
        time.sleep(3)
        
        logger.info("📌 尝试点击'全部评价'按钮")
        
        # 尝试多个选择器
        clicked = False
        for sel in ['text=全部评价', 'text=全部', '.comment-filter-item']:
            try:
                btn = dp.ele(sel, timeout=2)
                if btn:
                    btn.scroll.to_see()
                    time.sleep(1)
                    btn.click()
                    logger.info(f"✅ 点击按钮成功：{sel}")
                    clicked = True
                    break
            except Exception as e:
                logger.debug(f"尝试 {sel} 失败：{e}")
                continue
        
        if not clicked:
            logger.warning("⚠️ 未能点击评论按钮，继续尝试...")
        
        time.sleep(2)
        
        # 第二步：等待第一个真实请求，抓取模板参数
        logger.info("⏳ 等待评论数据包...")
        template_params = None
        
        for attempt in range(20):
            try:
                resp = dp.listen.wait(timeout=10)
                if resp is None:
                    continue
                
                if not hasattr(resp, 'response') or resp.response is None:
                    continue
                
                body = resp.response.body
                if isinstance(body, dict) and 'result' in body:
                    req = resp.request
                    template_params = {
                        'url': req.url,
                        'headers': dict(req.headers),
                        'postData': req.postData
                    }
                    logger.info(f"✅ 获取请求模板（第 {attempt+1} 次尝试）")
                    break
            except Exception as e:
                logger.debug(f"尝试获取模板失败：{e}")
                continue
        
        dp.listen.stop()
        
        if not template_params:
            logger.error("❌ 无法获取请求模板（可能被JD反爬虫）")
            return {
                "status": "error",
                "message": "无法获取页面请求模板，可能是网站反爬虫或链接错误",
                "reviews": [],
                "count": 0
            }
        
        # 第三步：解析 postData
        try:
            post_dict = parse_qs(template_params['postData'])
            post_single = {k: v[0] for k, v in post_dict.items()}
            body_json = json.loads(post_single['body'])
            logger.info(f"📝 解析成功，当前 pageNum={body_json.get('pageNum')}")
        except Exception as e:
            logger.error(f"❌ 解析 postData 失败：{e}")
            return {
                "status": "error",
                "message": f"解析请求失败：{str(e)}",
                "reviews": [],
                "count": 0
            }
        
        # 第四步：定义翻页函数
        def fetch_page(page_num):
            body_json['pageNum'] = str(page_num)
            body_json['pageSize'] = "20"
            body_json['isFirstRequest'] = "false"
            post_single['body'] = json.dumps(body_json, ensure_ascii=False)
            post_data_str = urlencode(post_single)
            
            js_code = f"""
            return new Promise((resolve) => {{
                fetch("{template_params['url']}", {{
                    method: "POST",
                    headers: {{"Content-Type": "application/x-www-form-urlencoded"}},
                    body: {json.dumps(post_data_str)},
                    credentials: "include"
                }})
                .then(r => r.json())
                .then(data => resolve(JSON.stringify(data)))
                .catch(e => resolve("ERROR:" + e.toString()));
            }});
            """
            return dp.run_js(js_code, as_expr=False)
        
        # 第五步：定义递归查找评论列表的函数
        def find_comment_list(obj):
            if isinstance(obj, list):
                if obj and isinstance(obj[0], dict) and 'commentInfo' in obj[0]:
                    return obj
                for item in obj:
                    result = find_comment_list(item)
                    if result:
                        return result
            elif isinstance(obj, dict):
                for v in obj.values():
                    result = find_comment_list(v)
                    if result:
                        return result
            return None
        
        # 第六步：翻页爬取
        all_reviews = []
        seen_keys = set()
        total = 0
        empty_count = 0
        
        logger.info(f"🤖 开始爬取评论（最多 {max_pages} 页，每页20条）")
        
        for page in range(1, max_pages + 1):
            try:
                raw = fetch_page(page)
                
                if raw is None or str(raw).startswith("ERROR"):
                    logger.debug(f"  [第{page}页] 请求失败")
                    empty_count += 1
                else:
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug(f"  [第{page}页] JSON 解析失败")
                        empty_count += 1
                        continue
                    
                    # 查找评论数据
                    datas = find_comment_list(data)
                    
                    if not datas:
                        logger.debug(f"  [第{page}页] 未找到新评论")
                        empty_count += 1
                    else:
                        count = 0
                        for item in datas:
                            try:
                                info = item['commentInfo']
                                key = info['userNickName'] + info['commentDate'] + info.get('commentData', '')[:10]
                                
                                if key in seen_keys:
                                    continue
                                
                                seen_keys.add(key)
                                all_reviews.append({
                                    "review_content": info.get('commentData', ''),
                                    "rating": int(info['commentScore']) if info.get('commentScore') else 0,
                                    "review_time": info.get('commentDate', ''),
                                    "product_model": info.get('productSpecifications', ''),
                                    "likes": int(info['buyCount']) if info.get('buyCount') else 0
                                })
                                count += 1
                            except (KeyError, TypeError) as e:
                                logger.debug(f"解析评论失败：{e}")
                                continue
                        
                        if count > 0:
                            empty_count = 0
                            total += count
                            logger.info(f"  ✅ [第{page}页] 新增 {count} 条 | 累计 {total} 条")
                        else:
                            empty_count += 1
                
                if empty_count >= 5:
                    logger.info(f"✅ 连续5页无新数据，判定已爬取完毕")
                    break
                
                time.sleep(0.5)
            
            except KeyboardInterrupt:
                logger.warning("⚠️ 用户中止爬虫")
                break
            except Exception as e:
                logger.error(f"  [第{page}页] 异常：{e}")
                empty_count += 1
                continue
        
        dp.quit()
        
        if total == 0:
            return {
                "status": "error",
                "message": "未能爬取到任何评论（可能链接错误或无评论）",
                "reviews": [],
                "count": 0
            }
        
        logger.info(f"✅ 爬虫完成！共 {total} 条评论")
        
        return {
            "status": "success",
            "reviews": all_reviews,
            "count": total,
            "message": f"成功爬取 {total} 条评论"
        }
    
    except Exception as e:
        logger.error(f"❌ 爬虫异常：{e}")
        return {
            "status": "error",
            "message": str(e),
            "reviews": [],
            "count": 0
        }


# ==================== API 端点 ====================

@app.route('/api/spider', methods=['POST'])
def api_spider():
    """
    仅爬虫接口
    
    请求：
    {
        "product_url": "https://item.jd.com/...",
        "max_pages": 50
    }
    """
    try:
        data = request.get_json()
        product_url = data.get('product_url')
        max_pages = data.get('max_pages', 50)
        
        if not product_url:
            return jsonify({
                "status": "error",
                "message": "缺少 product_url 参数"
            }), 400
        
        result = get_jd_reviews(product_url, max_pages)
        return jsonify(result), 200 if result['status'] == 'success' else 400
    
    except Exception as e:
        logger.error(f"爬虫API异常：{e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """
    分析接口（调用 analysis.py 和你的扣子插件）
    
    请求：
    {
        "reviews": [
            {"review_content": "...", "rating": 5, ...},
            ...
        ]
    }
    """
    try:
        data = request.get_json()
        reviews = data.get('reviews', [])
        
        if not reviews:
            return jsonify({
                "status": "error",
                "message": "缺少 reviews 数据"
            }), 400
        
        logger.info(f"📊 分析 {len(reviews)} 条评论...")
        
        if not analyze_reviews_main:
            return jsonify({
                "status": "error",
                "message": "分析模块未加载（缺少 analysis.py）"
            }), 500
        
        # 调用你的分析函数
        try:
            analysis_result = analyze_reviews_main(reviews)
            logger.info("✅ 分析完成")
            return jsonify({
                "status": "success",
                "analysis": analysis_result,
                "reviews_count": len(reviews)
            }), 200
        except Exception as e:
            logger.error(f"分析过程异常：{e}")
            return jsonify({
                "status": "error",
                "message": f"分析失败：{str(e)}"
            }), 500
    
    except Exception as e:
        logger.error(f"分析API异常：{e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/api/full-pipeline', methods=['POST'])
def api_full_pipeline():
    """
    完整管道：爬虫 + 分析
    
    请求：
    {
        "product_url": "https://item.jd.com/..."
    }
    
    返回：完整分析结果
    """
    try:
        data = request.get_json()
        product_url = data.get('product_url')
        
        if not product_url:
            return jsonify({
                "status": "error",
                "message": "缺少 product_url"
            }), 400
        
        logger.info("="*50)
        logger.info("🔄 开始完整流程")
        logger.info("="*50)
        
        # 步骤1：爬虫
        logger.info("[1/3] 爬取评论...")
        spider_result = get_jd_reviews(product_url)
        
        if spider_result['status'] != 'success':
            return jsonify(spider_result), 400
        
        reviews = spider_result['reviews']
        
        # 步骤2：分析
        logger.info("[2/3] 分析评论...")
        if not analyze_reviews_main:
            return jsonify({
                "status": "error",
                "message": "分析模块未加载"
            }), 500
        
        try:
            analysis_result = analyze_reviews_main(reviews)
        except Exception as e:
            logger.error(f"分析异常：{e}")
            return jsonify({
                "status": "error",
                "message": f"分析失败：{str(e)}"
            }), 500
        
        # 步骤3：整合返回
        logger.info("[3/3] 整合结果...")
        
        result = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "spider": {
                "reviews_count": len(reviews),
                "message": spider_result['message']
            },
            "analysis": analysis_result
        }
        
        logger.info("✅ 完整流程完成！")
        logger.info("="*50)
        
        return jsonify(result), 200
    
    except Exception as e:
        logger.error(f"完整流程异常：{e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        "status": "online",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0"
    }), 200


# ==================== 启动应用 ====================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"🚀 启动服务器，监听端口 {port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
