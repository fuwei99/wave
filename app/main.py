import time
import uuid
import json
import os
import asyncio
import base64
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Union

from . import config
from .wavespeed_client import WavespeedClient
from .r2_uploader import get_r2_uploader
from .utils import extract_prompt_from_messages, extract_params_from_prompt, extract_images_from_messages

app = FastAPI(title="Wavespeed to OpenAI API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models
class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    stream: Optional[bool] = False
    # 其他 OpenAI 参数可忽略

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: Dict[str, Any]
    finish_reason: str

class ChatCompletionResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]
    usage: Dict[str, int]

class ModelListResponse(BaseModel):
    object: str
    data: List[Dict[str, Any]]

# Auth Dependency
async def verify_api_key(authorization: str = Header(None)):
    if not config.API_KEY:
        return # No auth required if API_KEY not set
        
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid Authorization header format")
        
    token = authorization.replace("Bearer ", "")
    if token != config.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")

# Clients
wavespeed_client = WavespeedClient()
r2_uploader = get_r2_uploader()

@app.get("/v1/models", response_model=ModelListResponse)
async def list_models(auth: None = Depends(verify_api_key)):
    try:
        models_file = os.path.join(os.path.dirname(__file__), "models.json")
        with open(models_file, "r", encoding="utf-8") as f:
            models_data = json.load(f)
        return ModelListResponse(object="list", data=models_data)
    except Exception as e:
        print(f"Error listing models: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, auth: None = Depends(verify_api_key)):
    try:
        # Debug: Save payload
        with open("wavespeed2api/payload.txt", "w", encoding="utf-8") as f:
            json.dump(request.messages, f, indent=2, ensure_ascii=False)
            
        # 1. Extract prompt
        raw_prompt = extract_prompt_from_messages(request.messages)
        if not raw_prompt:
            raise HTTPException(status_code=400, detail="No prompt found in messages")
            
        # 2. Process tags (LoRA, size, format)
        cleaned_prompt, loras, params = extract_params_from_prompt(raw_prompt)
        
        # LoRA handling
        final_loras = None
        if "lora" in request.model.lower():
            final_loras = loras
            print(f"Model '{request.model}' supports LoRA. Found {len(loras)} LoRAs.")
        else:
            if loras:
                print(f"Model '{request.model}' does NOT support LoRA. Ignored {len(loras)} LoRAs found in prompt.")
        
        # Size handling
        width = params.get("width", 1536)
        height = params.get("height", 1536)
        size = f"{width}*{height}"
        
        # Output format handling
        output_format = params.get("output_format")
        
        # Seed handling
        seed = params.get("seed")
        
        # 3. Process Images (for image-edit)
        source_images = []
        if "edit" in request.model.lower():
            # 提取图片 URL
            print(f"DEBUG: Extracting images from messages for model {request.model}")
            # print(f"DEBUG: Messages: {json.dumps(request.messages, ensure_ascii=False)}")
            raw_images = extract_images_from_messages(request.messages)
            print(f"DEBUG: Extracted raw images: {raw_images}")
            
            for img_url in raw_images:
                if r2_uploader.is_enabled():
                    # 检查是否是 data URL (Base64)
                    if img_url.startswith("data:image"):
                        try:
                            # 提取 Base64 数据
                            header, encoded = img_url.split(",", 1)
                            mime_type = header.split(":")[1].split(";")[0]
                            image_bytes = base64.b64decode(encoded)
                            
                            # 上传到 R2
                            uploaded_url = r2_uploader.upload_image(image_bytes, mime_type)
                            if uploaded_url:
                                source_images.append(uploaded_url)
                            else:
                                print("Warning: Failed to upload base64 image to R2")
                                # Base64 太长，Wavespeed 可能不支持直接传，但如果没有 R2 也没办法
                                source_images.append(img_url)
                        except Exception as e:
                            print(f"Error processing base64 image: {e}")
                            source_images.append(img_url)
                    else:
                        # 普通 URL，尝试下载并上传到 R2 (以防原链接失效或不可访问)
                        uploaded_url = r2_uploader.upload_image_from_url(img_url)
                        if uploaded_url:
                            source_images.append(uploaded_url)
                        else:
                            source_images.append(img_url)
                else:
                    source_images.append(img_url)
            
            print(f"Model '{request.model}' is an edit model. Found {len(source_images)} source images.")
        
        # 4. Create Wavespeed task
        task_id = wavespeed_client.create_task(
            model_id=request.model,
            prompt=cleaned_prompt,
            size=size,
            loras=final_loras,
            output_format=output_format,
            seed=seed,
            images=source_images
        )
        
        # 5. Handle Response (Stream or Non-Stream)
        if request.stream:
            async def stream_generator():
                response_id = f"chatcmpl-{uuid.uuid4()}"
                created_time = int(time.time())
                
                # Send initial chunk
                chunk_data = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
                
                # Polling loop with Keep-Alive
                start_time = time.time()
                last_keepalive_time = start_time
                image_url = None
                
                while True:
                    # Check status
                    # Note: wavespeed_client.check_status is synchronous, so it might block slightly.
                    # In a high-concurrency async app, this should be run in a thread pool.
                    # For simplicity here, we call it directly as it's just a quick HTTP request.
                    result = wavespeed_client.check_status(task_id)
                    status = result.get("status")
                    
                    if status == "succeeded":
                        image_url = result.get("output")
                        break
                    elif status == "failed":
                        error_msg = result.get("error", "Unknown error")
                        # Send error as content or just stop? OpenAI stream usually doesn't have explicit error chunk format for mid-stream errors.
                        # We'll send it as content for visibility.
                        chunk_data["choices"][0]["delta"] = {"content": f"\n[Error: {error_msg}]"}
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                        break
                    
                    # Check timeout (e.g. 5 minutes)
                    if time.time() - start_time > 300:
                        chunk_data["choices"][0]["delta"] = {"content": "\n[Error: Timeout]"}
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                        break
                    
                    # Send Keep-Alive (space) every 10 seconds
                    if time.time() - last_keepalive_time >= 10:
                        chunk_data["choices"][0]["delta"] = {"content": " "} # Space as keep-alive
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                        last_keepalive_time = time.time()
                    
                    await asyncio.sleep(2) # Async sleep to yield control
                
                if image_url:
                    # Upload to R2 (if enabled)
                    final_image_url = image_url
                    if r2_uploader.is_enabled():
                        # This is also synchronous/blocking. Ideally run in thread pool.
                        uploaded_url = r2_uploader.upload_image_from_url(image_url)
                        if uploaded_url:
                            final_image_url = uploaded_url
                    
                    markdown_content = f"![image]({final_image_url})"
                    
                    # Send content chunk
                    chunk_data["choices"][0]["delta"] = {"content": markdown_content}
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                
                # Send final chunk
                chunk_data["choices"][0]["delta"] = {}
                chunk_data["choices"][0]["finish_reason"] = "stop"
                yield f"data: {json.dumps(chunk_data)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        else:
            # Non-streaming: Synchronous polling
            image_url = wavespeed_client.poll_result(task_id)
            
            # Upload to R2 (if enabled)
            final_image_url = image_url
            if r2_uploader.is_enabled():
                uploaded_url = r2_uploader.upload_image_from_url(image_url)
                if uploaded_url:
                    final_image_url = uploaded_url
            
            markdown_content = f"![image]({final_image_url})"
            
            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4()}",
                object="chat.completion",
                created=int(time.time()),
                model=request.model,
                choices=[
                    ChatCompletionResponseChoice(
                        index=0,
                        message={
                            "role": "assistant",
                            "content": markdown_content
                        },
                        finish_reason="stop"
                    )
                ],
                usage={
                    "prompt_tokens": len(cleaned_prompt),
                    "completion_tokens": len(markdown_content),
                    "total_tokens": len(cleaned_prompt) + len(markdown_content)
                }
            )
        
    except Exception as e:
        print(f"Error processing request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    return {"status": "ok"}