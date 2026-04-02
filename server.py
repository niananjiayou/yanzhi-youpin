
# spider_api.py - 仅爬虫服务，分析调用扣子插件

from flask import Flask, request, jsonify
from DrissionPage import ChromiumPage, ChromiumOptions
import json
import time
from urllib.parse import parse_qs, urlencode
import requests

app = Flask(__name__)

# ==================== 爬虫模块 ====================
class ProductSpider:
    def __init__(self):
        self.co = ChromiumOptions()
        self.co.set_browser_path(r'C:\Program Files\Google\Chrome\Application\chrome.exe')
        self.co.set_local_port(9333)
        self.co.set_user_data_path(r'D:\chrome_debug_profile')
        self.dp = ChromiumPage(self.co)
        self.all_reviews = []
        self.seen_keys = set()
    
    def run_spider(self, product_url):
        """主爬虫方法 - 完全基于你的 c.py 逻辑"""
        try:
            print(f"🔍 开始爬取: {product_url}")
            
            # 打开页面
            self.dp.listen.start('client.action')
            self.dp.get(product_url)
            time.sleep(3)
            
            # 点击"全部评价"
            for sel in ['text=全部评价', 'text=全部', '.comment-filter-item']:
                try:
                    btn = self.dp.ele(sel, timeout=3)
                    if btn:
                        btn.scroll.to_see()
                        time.sleep(1)
                        btn.click()
                        print(f"✅ 点击按钮: {sel}")
                        break
                except:
                    continue
            
            # 等待模板请求
            print("⏳ 等待第一个评论数据包...")
            template_params = None
            for _ in range(20):
                resp = self.dp.listen.wait(timeout=10)
                if resp is None:
                    break
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
                    print(f"✅ 抓到模板请求！")
                    break
            
            self.dp.listen.stop()
            
            if not template_params:
                print("❌ 未抓到模板请求")
                return None
            
            # 解析 postData
            post_dict = parse_qs(template_params['postData'])
            post_single = {k: v[0] for k, v in post_dict.items()}
            body_json = json.loads(post_single['body'])
            
            # 翻页爬取
            self._fetch_all_pages(template_params, body_json, post_single)
            
            return self.all_reviews if self.all_reviews else None
            
        except Exception as e:
            print(f"❌ 爬虫错误: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def _fetch_all_pages(self, template_params, body_json, post_single):
        """翻页爬取 - 完全基于你的 c.py"""
        max_pages = 50  # 防止超时
        max_empty = 5
        empty_count = 0
        
        for page in range(1, max_pages + 1):
            print(f"📄 第 {page} 页...", end="  ")
            
            body_json['pageNum'] = str(page)
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
            
            raw = self.dp.run_js(js_code, as_expr=False)
            result = self._parse_reviews(raw)
            
            if result > 0:
                empty_count = 0
            else:
                empty_count += 1
            
            if empty_count >= max_empty:
                print(f"\n✅ 连续 {max_empty} 页无新数据，停止爬取")
                break
            
            time.sleep(0.3)
    
    def _parse_reviews(self, raw_json_str):
        """解析评论 - 完全基于你的 c.py"""
        try:
            data = json.loads(raw_json_str)
            if str(data.get('code')) != '0':
                return -1
            
            datas = self._find_comment_list(data)
            if not datas:
                return 0
            
            count = 0
            for item in datas:
                try:
                    info = item['commentInfo']
                    key = info['userNickName'] + info['commentDate'] + info.get('commentData', '')[:10]
                    if key not in self.seen_keys:
                        self.seen_keys.add(key)
                        self.all_reviews.append({
                            "review_content": info.get('commentData', ''),
                            "rating": int(info['commentScore']) if info.get('commentScore') else 0,
                            "review_time": info.get('commentDate', ''),
                            "product_model": info.get('productSpecifications', ''),
                            "likes": int(info['buyCount']) if info.get('buyCount') else 0
                        })
                        count += 1
                except (KeyError, TypeError):
                    continue
            
            print(f"✅ 本页新增 {count} 条 | 累计 {len(self.all_reviews)} 条")
            return count
        except Exception as e:
            print(f"❌ 解析失败: {e}")
            return -1
    
    def _find_comment_list(self, obj):
        """递归查找评论列表 - 基于你的 c.py"""
        if isinstance(obj, list):
            if obj and isinstance(obj[0], dict) and 'commentInfo' in obj[0]:
                return obj
            for item in obj:
                result = self._find_comment_list(item)
                if result:
                    return result
        elif isinstance(obj, dict):
            for v in obj.values():
                result = self._find_comment_list(v)
                if result:
                    return result
        return None


# ==================== Flask 端点 ====================

@app.route('/spider', methods=['POST'])
def spider():
    """
    爬虫端点：仅爬取评论
    输入：{ "product_url": "..." }
    输出：{ "success": true, "reviews": [...] }
    """
    try:
        data = request.json
        product_url = data.get('product_url')
        
        if not product_url:
            return jsonify({'success': False, 'message': '缺少 product_url 参数'}), 400
        
        spider = ProductSpider()
        reviews = spider.run_spider(product_url)
        
        if not reviews:
            return jsonify({'success': False, 'message': '评论爬取失败或无评论'}), 400
        
        return jsonify({
            'success': True,
            'reviews': reviews
        }), 200
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """健康检查"""
    return jsonify({'status': 'ok'}), 200


if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
