#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地图片监控脚本 - 修正API端点重复问题
"""

import os
import sys
import time
import json
import hashlib
import logging
import base64
from datetime import datetime
from typing import List, Dict, Optional

import requests
from PIL import Image
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# ==================== 配置区域 ====================
class Config:
    # 监控设置
    MONITOR_DIR = r"D:/images"
    SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

    # Ollama配置
    OLLAMA_HOST = "http://127.0.0.1:11434"
    OLLAMA_MODEL = "llava:13b"

    # Dify配置
    DIFY_API_KEY = "dataset-3LlZK7Py2tWz3KJ3Q8qfEOsI"
    DIFY_KB_ID = "72dd4810-fea3-487b-9850-d50b82bcaaba"
    DIFY_BASE_URL = "http://localhost"

    # 从您的日志看，正确的端点是 /v1
    DIFY_API_PREFIX = "/v1"

    # 处理设置
    MAX_RETRY = 3
    RETRY_DELAY = 5
    REQUEST_TIMEOUT = 120


# ==================== 日志配置 ====================
def setup_logging():
    os.makedirs("logs", exist_ok=True)
    log_file = "logs/image_monitor.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger(__name__)


logger = setup_logging()


# ==================== 状态管理器 ====================
class StateManager:
    def __init__(self, state_file: str = "processed_files.json"):
        self.state_file = state_file
        self.processed_files = self.load_state()

    def load_state(self) -> Dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载状态文件失败: {e}")
                return {}
        return {}

    def save_state(self):
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.processed_files, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")

    def is_processed(self, file_path: str) -> bool:
        file_key = self._get_file_key(file_path)
        return file_key in self.processed_files

    def mark_processed(self, file_path: str, description: str = ""):
        file_key = self._get_file_key(file_path)
        self.processed_files[file_key] = {
            'path': file_path,
            'processed_time': datetime.now().isoformat(),
            'description': description[:100] if description else ""
        }
        self.save_state()

    def _get_file_key(self, file_path: str) -> str:
        try:
            stat = os.stat(file_path)
            file_info = f"{file_path}|{stat.st_mtime}|{stat.st_size}"
            return hashlib.md5(file_info.encode()).hexdigest()
        except Exception as e:
            logger.error(f"获取文件信息失败 {file_path}: {e}")
            return hashlib.md5(file_path.encode()).hexdigest()


# ==================== 图片处理器 ====================
class ImageProcessor:
    def __init__(self):
        self.state_manager = StateManager()

    def extract_image_info(self, image_path: str) -> Optional[str]:
        if not os.path.exists(image_path):
            return None

        try:
            with Image.open(image_path) as img:
                img.verify()
        except Exception as e:
            logger.error(f"图片文件损坏 {image_path}: {e}")
            return None

        for attempt in range(Config.MAX_RETRY):
            try:
                with open(image_path, 'rb') as f:
                    image_data = f.read()

                image_base64 = base64.b64encode(image_data).decode('utf-8')
                url = f"{Config.OLLAMA_HOST}/api/generate"

                prompt = """请用中文详细描述这张图片：
                1. 图片中的主要内容和场景
                2. 颜色、形状、纹理特征
                3. 可能的地点、时间、环境
                4. 情感氛围和整体感觉

                请用准确、自然的中文描述。"""

                payload = {
                    "model": Config.OLLAMA_MODEL,
                    "prompt": prompt,
                    "images": [image_base64],
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_predict": 600
                    }
                }

                logger.info(f"正在分析图片: {os.path.basename(image_path)}")
                response = requests.post(url, json=payload, timeout=Config.REQUEST_TIMEOUT)
                response.raise_for_status()

                result = response.json()
                description = result.get('response', '').strip()

                if description and len(description) > 30:
                    logger.info(f"✅ 成功生成中文描述")
                    return description

            except Exception as e:
                logger.warning(f"第{attempt + 1}次重试: {e}")
                time.sleep(Config.RETRY_DELAY)

        return None

    def upload_to_knowledge_base(self, image_path: str, description: str) -> bool:
        """上传到Dify知识库 - 修正的API端点"""
        if not description:
            return False

        # 准备文档内容
        file_name = os.path.basename(image_path)
        full_path = os.path.abspath(image_path)

        doc_content = f"""file_name: {file_name}
full_path: {full_path}
description: {description}
处理时间: {datetime.now().isoformat()}"""

        for attempt in range(Config.MAX_RETRY):
            try:
                # 关键修正：使用正确的API端点结构
                # 从 /v1/datasets 拼接，而不是 /v1/datasets/datasets
                url = f"{Config.DIFY_BASE_URL}{Config.DIFY_API_PREFIX}/datasets/{Config.DIFY_KB_ID}/document/create_by_text"

                headers = {
                    "Authorization": f"Bearer {Config.DIFY_API_KEY}",
                    "Content-Type": "application/json"
                }

                data = {
                    "name": file_name,
                    "text": doc_content,
                    "indexing_technique": "high_quality"
                }

                logger.info(f"📤 正在上传: {file_name}")
                logger.info(f"🌐 API地址: {url}")

                response = requests.post(url, json=data, headers=headers, timeout=30)
                logger.info(f"📊 响应状态: {response.status_code}")

                if response.status_code in [200, 201, 202]:
                    result = response.json()
                    logger.info(f"✅ 上传成功！文档ID: {result.get('id', '未知')}")
                    print(f"\n✅ 上传成功！")
                    print(f"📁 文件: {file_name}")
                    print(f"📍 路径: {full_path}")
                    return True
                else:
                    logger.error(f"❌ 上传失败: {response.text[:200]}")

            except Exception as e:
                logger.error(f"❌ 上传错误: {e}")
                time.sleep(Config.RETRY_DELAY)

        return False

    def process_image(self, image_path: str) -> bool:
        if not os.path.exists(image_path):
            return False

        if self.state_manager.is_processed(image_path):
            logger.info(f"文件已处理，跳过")
            return True

        ext = os.path.splitext(image_path)[1].lower()
        if ext not in Config.SUPPORTED_FORMATS:
            return False

        print(f"\n🔍 处理图片: {os.path.basename(image_path)}")

        # 生成描述
        description = self.extract_image_info(image_path)
        if not description:
            print("❌ 描述生成失败")
            return False

        print(f"✅ 描述生成完成")

        # 上传
        if self.upload_to_knowledge_base(image_path, description):
            self.state_manager.mark_processed(image_path, description)
            print(f"🎉 处理完成！")
            return True
        else:
            print("❌ 上传失败")
            return False


# ==================== 文件监控器 ====================
class ImageFileHandler(FileSystemEventHandler):
    def __init__(self, processor: ImageProcessor):
        self.processor = processor

    def on_created(self, event):
        if event.is_directory:
            return

        file_path = event.src_path
        ext = os.path.splitext(file_path)[1].lower()

        if ext in Config.SUPPORTED_FORMATS:
            print(f"\n📱 检测到新图片: {os.path.basename(file_path)}")
            time.sleep(2)
            self.processor.process_image(file_path)

    def on_moved(self, event):
        if event.is_directory:
            return

        dest_path = event.dest_path
        ext = os.path.splitext(dest_path)[1].lower()

        if ext in Config.SUPPORTED_FORMATS and os.path.exists(dest_path):
            print(f"\n📱 检测到移动图片: {os.path.basename(dest_path)}")
            time.sleep(2)
            self.processor.process_image(dest_path)


# ==================== 主程序 ====================
def main():
    print(f"""
    {'=' * 60}
    📸 本地图片监控服务
    📁 监控目录: {Config.MONITOR_DIR}
    🤖 分析模型: {Config.OLLAMA_MODEL}
    🧠 知识库ID: {Config.DIFY_KB_ID}
    🌐 API前缀: {Config.DIFY_API_PREFIX}
    {'=' * 60}
    """)

    # 检查目录
    if not os.path.exists(Config.MONITOR_DIR):
        os.makedirs(Config.MONITOR_DIR, exist_ok=True)
        print(f"📁 已创建监控目录")

    # 检查Ollama
    print("🔧 检查Ollama服务...")
    try:
        response = requests.get(f"{Config.OLLAMA_HOST}/api/tags", timeout=5)
        if response.status_code == 200 and Config.OLLAMA_MODEL in [m.get('name', '') for m in
                                                                   response.json().get('models', [])]:
            print(f"✅ Ollama服务正常")
        else:
            print(f"⚠️  Ollama服务异常")
    except:
        print("❌ 无法连接Ollama服务")
        return

    # 初始化处理器
    processor = ImageProcessor()

    # 文件监控
    event_handler = ImageFileHandler(processor)
    observer = Observer()
    observer.schedule(event_handler, Config.MONITOR_DIR, recursive=True)

    try:
        print(f"\n🚀 开始监控...")
        observer.start()

        # 扫描现有文件
        processed_count = 0
        failed_count = 0

        for root, dirs, files in os.walk(Config.MONITOR_DIR):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in Config.SUPPORTED_FORMATS:
                    file_path = os.path.join(root, file)
                    if not processor.state_manager.is_processed(file_path):
                        if processor.process_image(file_path):
                            processed_count += 1
                        else:
                            failed_count += 1

        print(f"\n📊 扫描完成: ✅{processed_count} ❌{failed_count}")
        print(f"\n🎯 进入监控模式...")
        print(f"⏸️  按 Ctrl+C 停止")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print(f"\n🛑 停止监控")
    except Exception as e:
        print(f"\n❌ 错误: {e}")
    finally:
        observer.stop()
        observer.join()
        print("👋 服务已停止")


# ==================== 命令行接口 ====================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="本地图片监控服务")
    parser.add_argument("--dir", help=f"监控目录")
    parser.add_argument("--scan", action="store_true", help="只扫描")
    parser.add_argument("--test-api", action="store_true", help="测试API连接")

    args = parser.parse_args()

    if args.dir:
        Config.MONITOR_DIR = args.dir

    if args.test_api:
        # 测试API连接
        print("=== 测试Dify API连接 ===")

        # 测试正确的端点
        test_url = f"{Config.DIFY_BASE_URL}{Config.DIFY_API_PREFIX}/datasets"
        headers = {"Authorization": f"Bearer {Config.DIFY_API_KEY}"}

        print(f"测试端点: {test_url}")
        try:
            response = requests.get(test_url, headers=headers, timeout=5)
            print(f"状态码: {response.status_code}")
            print(f"响应: {response.text[:200]}")
        except Exception as e:
            print(f"连接失败: {e}")

    elif args.scan:
        # 只扫描模式
        processor = ImageProcessor()
        processed_count = 0
        failed_count = 0

        for root, dirs, files in os.walk(Config.MONITOR_DIR):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in Config.SUPPORTED_FORMATS:
                    file_path = os.path.join(root, file)
                    if processor.process_image(file_path):
                        processed_count += 1
                    else:
                        failed_count += 1

        print(f"\n📊 扫描完成: ✅{processed_count} ❌{failed_count}")
    else:
        main()