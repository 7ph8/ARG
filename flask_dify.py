import requests
import json
import urllib3
import os
import re
import subprocess  # 用于唤起本地程序
import platform  # 判断系统类型
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import urllib.parse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
CORS(app)  # 解决跨域问题

# 配置图片根目录
IMAGE_ROOT_DIR = r"D:\images"
os.makedirs(IMAGE_ROOT_DIR, exist_ok=True)
app.config['IMAGE_ROOT_DIR'] = IMAGE_ROOT_DIR

# Dify配置
DIFY_API_KEY = "app-t3gRMl4DVDTCFALU7squi3Xa"
DIFY_AGENT_ID = "067b7bb4-be2d-43eb-adf5-d8c73ae0bb5d"
DIFY_API_URL = "http://localhost/v1"

# 路径提取正则表达式（匹配Windows绝对路径）
PATH_PATTERN = re.compile(
    r'完整路径[:：]\s*([A-Za-z]:\\[^<>\|"?\n\r]+?\.(jpg|jpeg|png|gif|bmp))',
    re.IGNORECASE
)


# ======================== 新增：提取图片完整路径工具 ========================
@app.route("/extract-image-path", methods=["POST"])
def extract_image_path():
    """
    从知识库文档内容中提取图片完整路径
    请求参数：{"document_content": "知识库文档的完整内容"}
    返回结果：{"success": bool, "message": "提示信息", "image_path": "提取的完整路径", "escaped_path": "转义后的路径"}
    """
    try:
        # 获取请求参数
        data = request.get_json()
        document_content = data.get("document_content", "").strip()

        # 验证参数
        if not document_content:
            return jsonify({
                "success": False,
                "message": "文档内容不能为空",
                "image_path": "",
                "escaped_path": ""
            })

        # 使用正则提取完整路径
        match = PATH_PATTERN.search(document_content)
        if not match:
            # 备用匹配：直接匹配所有Windows图片路径
            backup_pattern = re.compile(r'([A-Za-z]:\\[^<>\|"?\n\r]+?\.(jpg|jpeg|png|gif|bmp))', re.IGNORECASE)
            match = backup_pattern.search(document_content)

        if not match:
            return jsonify({
                "success": False,
                "message": "未从文档中提取到有效的图片完整路径",
                "image_path": "",
                "escaped_path": ""
            })

        # 提取原始路径
        image_path = match.group(1).strip()

        # 验证路径格式
        if not os.path.isabs(image_path):
            return jsonify({
                "success": False,
                "message": f"提取的路径不是绝对路径：{image_path}",
                "image_path": "",
                "escaped_path": ""
            })

        # 生成转义后的路径（用于JSON传输）
        escaped_path = image_path.replace("\\", "\\\\")

        # 验证文件是否存在
        if os.path.exists(image_path):
            message = f"成功提取图片完整路径：{image_path}"
        else:
            message = f"提取到路径但文件不存在：{image_path}"

        print(f"【路径提取】原始路径：{image_path} | 转义路径：{escaped_path}")

        return jsonify({
            "success": True,
            "message": message,
            "image_path": image_path,
            "escaped_path": escaped_path
        })

    except Exception as e:
        error_detail = f"路径提取失败：{str(e)}"
        print(f"【错误】{error_detail}")
        return jsonify({
            "success": False,
            "message": error_detail,
            "image_path": "",
            "escaped_path": ""
        })


# ======================== 原有Dify调用函数 ========================
def query_dify_agent(user_query: str) -> dict:
    """调用Dify，返回解析后的图片信息（包含HTTP图片链接）"""
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY.strip()}",
        "Content-Type": "application/json"
    }
    payload = {
        "agent_id": DIFY_AGENT_ID,
        "inputs": {},
        "query": user_query,
        "response_mode": "streaming",
        "user": "test_user_001",
        "retrieval_config": {
            "retrieval_enabled": True,
            "top_k": 3,
            "score_threshold": 0.5
        }
    }

    try:
        response = requests.post(
            DIFY_API_URL,
            headers=headers,
            data=json.dumps(payload),
            verify=False,
            stream=True,
            timeout=30
        )
        response.raise_for_status()

        full_answer = ""
        for line in response.iter_lines():
            if line:
                line_data = line.decode("utf-8").lstrip("data: ").strip()
                if line_data == "[DONE]":
                    break
                if line_data:
                    try:
                        json_data = json.loads(line_data)
                        if "content" in json_data:
                            full_answer += json_data["content"]
                    except json.JSONDecodeError:
                        continue

        full_answer = full_answer.strip().strip('`').strip()
        try:
            result = json.loads(full_answer)
            image_path = result.get("image_path", "").strip()
            image_http_url = ""
            if image_path and os.path.exists(image_path):
                image_filename = os.path.basename(image_path)
                image_http_url = f"http://127.0.0.1:5000/static-images/{image_filename}"

            return {
                "success": True,
                "image_path": image_path,
                "image_url": image_http_url,
                "description": result.get("description", "已找到对应的图片").strip()
            }
        except json.JSONDecodeError:
            return {
                "success": False,
                "image_path": "",
                "image_url": "",
                "description": full_answer or "未找到有效的图片信息"
            }
    except Exception as e:
        return {
            "success": False,
            "image_path": "",
            "image_url": "",
            "description": f"调用失败：{str(e)}"
        }


# ======================== 前端查询接口 ========================
@app.route("/query-image", methods=["POST"])
def query_image():
    data = request.get_json()
    user_query = data.get("query", "").strip()
    if not user_query:
        return jsonify({
            "success": False,
            "image_url": "",
            "description": "查询内容不能为空"
        })

    result = query_dify_agent(user_query)
    return jsonify(result)


# ======================== 静态图片访问接口 ========================
@app.route('/static-images/<path:filename>')
def serve_static_image(filename):
    try:
        return send_from_directory(
            app.config['IMAGE_ROOT_DIR'],
            filename,
            as_attachment=False,
            mimetype=f"image/{filename.split('.')[-1].lower()}" if '.' in filename else 'image/jpeg'
        )
    except Exception as e:
        return jsonify({"error": f"图片不存在或访问失败：{str(e)}"}), 404


# ======================== 打开本地图片接口 ========================
@app.route("/open-local-image", methods=["POST"])
def open_local_image():
    """
    接收本地完整路径，直接唤起系统默认图片查看器打开文件
    请求参数：{"local_path": "C:\\Users\\温ates0926\\Desktop\\images\\1.jpg"}
    返回结果：{"success": bool, "message": "提示信息"}
    """
    try:
        # 获取请求中的本地路径
        data = request.get_json()
        local_path = data.get("local_path", "").strip()

        # 路径转义处理（兼容单/双反斜杠、正斜杠）
        local_path = local_path.replace("\\\\", "\\").replace("/", "\\")
        # 打印日志，方便排查实际接收的路径
        print(f"【调试】接收的本地路径：{local_path}")

        # 验证参数
        if not local_path:
            return jsonify({
                "success": False,
                "message": "本地图片路径不能为空"
            })

        # 验证文件是否存在
        if not os.path.exists(local_path):
            return jsonify({
                "success": False,
                "message": f"文件不存在：{local_path}"
            })

        # 验证是否为图片文件
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp']
        file_ext = os.path.splitext(local_path)[1].lower()
        if file_ext not in image_extensions:
            return jsonify({
                "success": False,
                "message": f"不是有效的图片文件（仅支持{','.join(image_extensions)}）"
            })

        # Windows下用os.startfile打开
        if platform.system() == "Windows":
            try:
                os.startfile(local_path)
            except PermissionError:
                subprocess.run(['powershell', '-Command', f'Start-Process "{local_path}" -Verb RunAs'], shell=True)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", local_path])
        else:
            subprocess.Popen(["xdg-open", local_path])

        return jsonify({
            "success": True,
            "message": f"已成功打开本地图片：{local_path}"
        })

    except Exception as e:
        error_detail = f"打开失败：{str(e)}，路径：{local_path}"
        print(f"【错误】{error_detail}")
        return jsonify({
            "success": False,
            "message": error_detail
        })


# ======================== 程序入口 ========================
if __name__ == "__main__":
    # 启动Flask服务
    app.run(host="0.0.0.0", port=5000, debug=True)