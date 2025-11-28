import re
from typing import List, Dict, Any, Tuple, Optional

def extract_prompt_from_messages(messages: List[Dict[str, Any]]) -> str:
    """
    从 OpenAI 格式的 messages 中提取最后的 prompt。
    简单策略：取最后一条 user 消息的内容。
    """
    if not messages:
        return ""
        
    # 倒序查找最后一条 user 消息
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str):
                return content
            elif isinstance(content, list):
                # 处理多模态内容（虽然这里只提取文本）
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                return "".join(text_parts)
    
    return ""

def extract_params_from_prompt(prompt: str) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    """
    从 Prompt 中提取 LoRA 标签和其他参数，并返回清洗后的 Prompt。
    
    支持标签:
    - <lora:url:scale> 或 <lora:url>
    - <height:1280>
    - <width:980>
    - <output_format:jpeg>
    
    Returns:
        (cleaned_prompt, loras, params)
    """
    loras = []
    params = {}
    
    # 1. 提取 LoRA
    # 正则匹配 <lora:url:scale>
    lora_pattern = r'<lora:([^>]+?)(?::([\d.]+))?>'
    
    def lora_replace_callback(match):
        url = match.group(1)
        scale_str = match.group(2)
        
        try:
            scale = float(scale_str) if scale_str else 1.0
        except ValueError:
            scale = 1.0
            
        loras.append({
            "path": url,
            "scale": scale
        })
        return "" # 删除标签

    prompt = re.sub(lora_pattern, lora_replace_callback, prompt)
    
    # 2. 提取其他参数
    # 匹配 <key:value>
    param_pattern = r'<(\w+):([^>]+)>'
    
    def param_replace_callback(match):
        key = match.group(1).lower()
        value = match.group(2).strip()
        
        if key in ["height", "width", "seed"]:
            try:
                params[key] = int(value)
            except ValueError:
                pass # 忽略无效数值
        elif key == "output_format":
            params[key] = value
            
        return "" # 删除标签

    prompt = re.sub(param_pattern, param_replace_callback, prompt)
    
    # 清理可能多余的空格
    cleaned_prompt = re.sub(r'\s+', ' ', prompt).strip()
    
    return cleaned_prompt, loras, params

def extract_images_from_messages(messages: List[Dict[str, Any]]) -> List[str]:
    """
    从最后一条 user 消息中提取图片 URL。
    支持:
    1. Markdown 格式: ![alt](url)
    2. OpenAI 格式: {"type": "image_url", "image_url": {"url": "..."}}
    """
    images = []
    if not messages:
        return images
        
    # 倒序查找最后一条 user 消息
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            
            # 1. 处理字符串内容 (Markdown 图片)
            if isinstance(content, str):
                # 匹配 ![...](url)
                markdown_pattern = r'!\[.*?\]\((https?://[^)]+)\)'
                matches = re.findall(markdown_pattern, content)
                images.extend(matches)
                
            # 2. 处理列表内容 (OpenAI 格式)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url")
                            if url:
                                images.append(url)
                        elif part.get("type") == "text":
                            # 同时也检查 text 部分是否包含 Markdown 图片
                            text = part.get("text", "")
                            markdown_pattern = r'!\[.*?\]\((https?://[^)]+)\)'
                            matches = re.findall(markdown_pattern, text)
                            images.extend(matches)
            
            # 只处理最后一条 user 消息
            break
            
    return images