import boto3
import hashlib
import time
import requests
from typing import Optional
from botocore.exceptions import ClientError
from . import config

class R2Uploader:
    """Cloudflare R2 图片上传工具"""
    
    def __init__(self):
        self.enabled = config.R2_ENABLED
        self.client = None
        self.bucket_name = config.R2_BUCKET_NAME
        self.public_url = config.R2_PUBLIC_URL.rstrip('/')
        
        if self.enabled:
            if not all([
                config.R2_ACCOUNT_ID,
                config.R2_ACCESS_KEY_ID,
                config.R2_SECRET_ACCESS_KEY,
                config.R2_BUCKET_NAME,
                config.R2_PUBLIC_URL
            ]):
                print("WARNING: R2_ENABLED is true but R2 configuration is incomplete. R2 upload will be disabled.")
                self.enabled = False
            else:
                try:
                    # 初始化 S3 客户端（R2 兼容 S3 API）
                    self.client = boto3.client(
                        's3',
                        endpoint_url=f'https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
                        aws_access_key_id=config.R2_ACCESS_KEY_ID,
                        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
                        region_name='auto'  # R2 使用 'auto' 作为区域
                    )
                    print(f"R2 Uploader initialized successfully. Bucket: {self.bucket_name}")
                except Exception as e:
                    print(f"ERROR: Failed to initialize R2 client: {e}")
                    self.enabled = False
    
    def _generate_filename(self, image_bytes: bytes, mime_type: str) -> str:
        """
        根据图片内容生成唯一文件名
        使用 MD5 哈希 + 时间戳确保唯一性
        """
        # 获取文件扩展名
        ext_map = {
            'image/png': 'png',
            'image/jpeg': 'jpg',
            'image/jpg': 'jpg',
            'image/gif': 'gif',
            'image/webp': 'webp',
            'image/bmp': 'bmp',
            'image/svg+xml': 'svg'
        }
        ext = ext_map.get(mime_type.lower(), 'png')
        
        # 生成内容哈希
        content_hash = hashlib.md5(image_bytes).hexdigest()[:16]
        
        # 添加时间戳确保唯一性
        timestamp = int(time.time() * 1000)
        
        # 生成文件名：images/年月/哈希_时间戳.扩展名
        year_month = time.strftime('%Y%m')
        filename = f"images/{year_month}/{content_hash}_{timestamp}.{ext}"
        
        return filename
    
    def upload_image_from_url(self, image_url: str) -> Optional[str]:
        """
        从 URL 下载图片并上传到 R2
        """
        if not self.enabled:
            return image_url # 如果未启用，直接返回原 URL
            
        try:
            print(f"Downloading image from {image_url}...")
            response = requests.get(image_url, timeout=30)
            response.raise_for_status()
            
            image_bytes = response.content
            mime_type = response.headers.get('content-type', 'image/png')
            
            return self.upload_image(image_bytes, mime_type)
        except Exception as e:
            print(f"ERROR: Failed to download/upload image from URL: {e}")
            return image_url # 失败时回退到原 URL

    def upload_image(self, image_bytes: bytes, mime_type: str) -> Optional[str]:
        """
        上传图片到 R2
        """
        if not self.enabled:
            return None
        
        try:
            filename = self._generate_filename(image_bytes, mime_type)
            
            # 上传到 R2
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=filename,
                Body=image_bytes,
                ContentType=mime_type,
                CacheControl='public, max-age=31536000',  # 缓存一年
            )
            
            # 生成公开访问 URL
            image_url = f"{self.public_url}/{filename}"
            
            print(f"Image uploaded to R2: {image_url}")
            return image_url
            
        except ClientError as e:
            print(f"ERROR: Failed to upload image to R2: {e}")
            return None
        except Exception as e:
            print(f"ERROR: Unexpected error during R2 upload: {e}")
            return None
    
    def is_enabled(self) -> bool:
        """检查 R2 上传是否已启用"""
        return self.enabled

# 全局单例
_r2_uploader_instance: Optional[R2Uploader] = None

def get_r2_uploader() -> R2Uploader:
    """获取 R2 上传器单例"""
    global _r2_uploader_instance
    if _r2_uploader_instance is None:
        _r2_uploader_instance = R2Uploader()
    return _r2_uploader_instance