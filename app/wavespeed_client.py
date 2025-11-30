import requests
import time
import json
import random
from . import config

class WavespeedClient:
    def __init__(self):
        self.api_url = config.WAVESPEED_API_URL
        self.cookies = config.WAVESPEED_COOKIE # Now a list
        self.current_cookie_index = 0
        self.token_failure_counts = {} # 记录每个 token 的连续失败次数
        
        self.base_headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json",
            "sec-ch-ua": "\"Chromium\";v=\"142\", \"Google Chrome\";v=\"142\", \"Not_A Brand\";v=\"99\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "Referer": config.WAVESPEED_REFERER,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
        }

    def _get_token_and_headers(self):
        """获取当前轮询的 Token 和 Headers"""
        if not self.cookies:
            raise Exception("No Wavespeed cookies configured.")
            
        # Round-robin selection
        # 确保索引在有效范围内 (防止列表变动后越界)
        self.current_cookie_index = self.current_cookie_index % len(self.cookies)
        
        cookie = self.cookies[self.current_cookie_index]
        # 指向下一个
        self.current_cookie_index = (self.current_cookie_index + 1) % len(self.cookies)
        
        headers = self.base_headers.copy()
        headers["cookie"] = cookie
        return cookie, headers

    def _handle_token_error(self, token, is_insufficient_credits=False):
        """处理 Token 错误，维护失败计数"""
        if is_insufficient_credits:
            count = self.token_failure_counts.get(token, 0) + 1
            self.token_failure_counts[token] = count
            print(f"Token ...{token[-10:]} failed with insufficient credits. Count: {count}/3")
            
            if count >= 3:
                print(f"Token ...{token[-10:]} removed due to consecutive insufficient credits errors.")
                if token in self.cookies:
                    self.cookies.remove(token)
                    # 如果移除后列表为空
                    if not self.cookies:
                        print("CRITICAL: All Wavespeed tokens have been exhausted!")
        else:
            # 如果是其他成功或无关错误，可以在这里重置计数？
            # 只有成功时才重置比较安全，见 create_task 逻辑
            pass

    def create_task(self, model_id: str, prompt: str, size: str = "1536*1536", loras: list = None, output_format: str = None, seed: int = None, images: list = None) -> str:
        """
        创建生图/修图任务
        返回 task_id
        """
        # 如果未提供 seed，生成一个随机 seed (0 - 2147483647)
        if seed is None or seed < 0:
            seed = random.randint(0, 2147483647)
            
        payload = {
            "enable_base64_output": False,
            "enable_sync_mode": False,
            "prompt": prompt,
            "seed": seed
        }
        
        # 只有非 image-edit 任务才需要 size (或者 image-edit 也可以传，但通常由原图决定)
        # 这里为了保险，如果 images 为空，则传递 size
        if not images:
            payload["size"] = size
        
        if loras:
            payload["loras"] = loras
            print(f"Adding {len(loras)} LoRAs to task.")
            
        if output_format:
            payload["output_format"] = output_format
            print(f"Setting output format to: {output_format}")
            
        if images:
            payload["images"] = images
            print(f"Adding {len(images)} source images for editing.")
        
        # 构造特定模型的 URL
        # 默认 URL 是 .../wavespeed-ai/z-image/turbo
        # 我们需要替换最后的部分为 model_id
        base_url = self.api_url.rsplit('/model_run/', 1)[0] + '/model_run/'
        target_url = base_url + model_id
        
        print(f"Creating Wavespeed task for model {model_id} with prompt: {prompt}, seed: {seed}")
        
        # 尝试次数上限，防止死循环。设置为当前 token 数量的 2 倍，确保每个都能试到
        max_attempts = max(len(self.cookies) * 2, 5)
        attempts = 0
        last_error = None

        while attempts < max_attempts and self.cookies:
            attempts += 1
            token, headers = self._get_token_and_headers()
            
            try:
                response = requests.post(target_url, headers=headers, json=payload)
                
                # 检查是否是额度不足错误 (Code 400 + 特定 Message)
                if response.status_code == 400:
                    try:
                        err_data = response.json()
                        err_msg = err_data.get("message", "")
                        if "Insufficient credits" in err_msg:
                            self._handle_token_error(token, is_insufficient_credits=True)
                            last_error = f"Insufficient credits for token ...{token[-10:]}"
                            continue # 尝试下一个 token
                    except:
                        pass # JSON 解析失败，继续走下面的 raise_for_status

                response.raise_for_status()
                
                # 请求成功，重置该 token 的失败计数
                self.token_failure_counts[token] = 0
                
                data = response.json()
                task_id = data.get("id")
                if not task_id:
                    raise Exception(f"Failed to get task ID from response: {data}")
                return task_id

            except Exception as e:
                print(f"Error creating task with token ...{token[-10:]}: {e}")
                if 'response' in locals():
                    print(f"Response content: {response.text}")
                last_error = e
                # 如果不是额度问题，但也失败了，我们依然尝试下一个 token，提高成功率
                continue
        
        # 如果循环结束还没成功
        raise Exception(f"Failed to create task after {attempts} attempts. Last error: {last_error}")

    def check_status(self, task_id: str) -> dict:
        """
        检查任务状态
        返回: {"status": "...", "output": "url" or None, "error": "..."}
        """
        result_url = f"https://wavespeed.ai/center/default/api/v1/predictions/{task_id}/result"
        
        try:
            _, headers = self._get_token_and_headers()
            response = requests.get(result_url, headers=headers)
            response.raise_for_status()
            resp_json = response.json()
            
            # 兼容不同的返回结构
            if "data" in resp_json:
                data = resp_json["data"]
            else:
                data = resp_json

            status = data.get("status")
            
            if status in ["succeeded", "completed"]:
                outputs = data.get("outputs", [])
                has_nsfw = data.get("has_nsfw_contents", [])
                if has_nsfw:
                    print(f"Warning: Task {task_id} has NSFW contents: {has_nsfw}")
                
                if outputs:
                    return {"status": "succeeded", "output": outputs[0]}
                else:
                    return {"status": "failed", "error": "Task succeeded but no outputs found."}
            elif status == "failed":
                error_msg = data.get("error", "Unknown error")
                return {"status": "failed", "error": error_msg}
            else:
                return {"status": status} # processing, created, etc.
                
        except Exception as e:
            print(f"Error checking status: {e}")
            return {"status": "error", "error": str(e)}

    def poll_result(self, task_id: str, timeout: int = 120) -> str:
        """
        轮询任务结果 (同步阻塞版本)
        返回图片 URL
        """
        print(f"Polling result for task: {task_id}")
        start_time = time.time()
        while time.time() - start_time < timeout:
            result = self.check_status(task_id)
            status = result.get("status")
            
            if status == "succeeded":
                return result.get("output")
            elif status == "failed":
                raise Exception(f"Task failed: {result.get('error')}")
            elif status == "error":
                # 网络错误等，继续重试
                pass
            
            time.sleep(2)
        
        raise Exception("Polling timeout")