# """
# API for uploading and downloading files (audio, text, JSON) to blob storage.
# Production-ready: FastAPI, Pydantic, logging, error handling, env config.
# """
# import os
# import base64
# import logging
# from typing import Optional
# from fastapi import FastAPI, UploadFile, File, Form, HTTPException, status, Body, Depends
# from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
# from pydantic import BaseModel
# from dotenv import load_dotenv
# import requests
# import json
#
# # Load environment variables
# load_dotenv()
#
# # Config from environment
# UPLOAD_URL = os.getenv("BLOB_UPLOAD_URL", "https://file-qc.vitalaxis.net/api/v4/upload/")
# DOWNLOAD_URL = os.getenv("BLOB_DOWNLOAD_URL", "https://file-qc.vitalaxis.net/api/v4/download/")
# HEADERS = {
#     "x-va-hash": os.getenv("BLOB_X_VA_HASH", "9QeX1fe00Ik2/xYgwvBVKBB5sgyhp7YNnJOpcqQ3wqwaAYh7lmGyAf3AW1/xTpCkaqiT/pRUknK09F8t535VnQ=="),
#     "x-va-transaction-id": os.getenv("BLOB_X_VA_TRANSACTION_ID", "123456789"),
#     "x-va-senderagent-id": os.getenv("BLOB_X_VA_SENDERAGENT_ID", "24835B46-8284-CD51-7E45-5775FEBDA5A9"),
#     "Content-Type": "application/json"
# }
#
# # Logging setup
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger("blob_api")
#
# app = FastAPI(title="VoiclaimBot Blob API", version="1.0")
#
# class UploadResponse(BaseModel):
#     fileid: str
#     fileName: str
#     message: Optional[str] = None
#
# class DownloadRequest(BaseModel):
#     fileid: str
#     version: Optional[str] = None  # Optional version field
#
# class SendJsonRequest(BaseModel):
#     fileid: str
#     target_url: Optional[str] = None  # Optional override for destination
#
# class UpdateJsonRequest(BaseModel):
#     fileid: str
#     updated_json: dict
#
# @app.post("/blob/upload", response_model=UploadResponse)
# async def upload_blob(
#     file: UploadFile = File(...),
#     file_type: str = Form(..., description="Type: audio, text, or json")
# ) -> UploadResponse:
#     """Upload a file (audio, text, or JSON) to blob storage."""
#     try:
#         content = await file.read()
#         encoded_content = base64.b64encode(content).decode("utf-8")
#         payload = {
#             "fileName": file.filename,
#             "fileContent": encoded_content
#         }
#         response = requests.post(UPLOAD_URL, headers=HEADERS, json=payload)
#         response.raise_for_status()
#         result = response.json()
#         logger.info(f"Uploaded {file.filename} to blob. Result: fileid={result.get('fileid')}")
#         return UploadResponse(fileid=result["fileid"], fileName=file.filename, message="Upload successful.")
#     except Exception as e:
#         logger.error(f"Upload failed: {e}")
#         raise HTTPException(status_code=500, detail="Upload failed.")
#
# @app.post("/blob/download")
# def download_blob(req: DownloadRequest) -> FileResponse:
#     """Download a file from blob storage by fileid and optional version."""
#     try:
#         payload = {"id": req.fileid}
#         if req.version:
#             payload["version"] = req.version
#         response = requests.post(DOWNLOAD_URL, headers=HEADERS, json=payload)
#         response.raise_for_status()
#         result = response.json()
#         file_name = result.get("fileName", "downloaded.bin")
#         file_content = result.get("fileContent")
#         if not file_content:
#             logger.error("No file content found in blob.")
#             raise HTTPException(status_code=404, detail="No file content found.")
#         decoded_data = base64.b64decode(file_content)
#         temp_path = f"/tmp/{file_name}"
#         with open(temp_path, "wb") as f:
#             f.write(decoded_data)
#         logger.info(f"Downloaded {file_name} from blob.")
#         response = FileResponse(temp_path, filename=file_name)
#         # Clean up temp file after response is sent
#         def cleanup():
#             try:
#                 os.remove(temp_path)
#             except Exception:
#                 pass
#         response.call_on_close(cleanup)
#         return response
#     except Exception as e:
#         logger.error(f"Download failed: {e}")
#         raise HTTPException(status_code=500, detail="Download failed.")
#
# @app.post("/blob/send_json")
# def send_json_to_app(req: SendJsonRequest) -> dict:
#     """Send a JSON file from blob to an external application via API."""
#     try:
#         payload = {"id": req.fileid}
#         response = requests.post(DOWNLOAD_URL, headers=HEADERS, json=payload)
#         response.raise_for_status()
#         result = response.json()
#         file_content = result.get("fileContent")
#         if not file_content:
#             raise HTTPException(status_code=404, detail="No file content found.")
#         decoded_data = base64.b64decode(file_content)
#         try:
#             json_data = json.loads(decoded_data.decode("utf-8"))
#         except Exception as e:
#             logger.error(f"Failed to decode JSON: {e}")
#             raise HTTPException(status_code=400, detail="Invalid JSON file content.")
#         target_url = req.target_url or os.getenv("EXTERNAL_APP_URL")
#         if not target_url:
#             raise HTTPException(status_code=400, detail="No target URL provided.")
#         ext_response = requests.post(target_url, json=json_data)
#         ext_response.raise_for_status()
#         return {"status": "success", "detail": ext_response.text}
#     except Exception as e:
#         logger.error(f"Send JSON failed: {e}")
#         raise HTTPException(status_code=500, detail="Send JSON failed.")
#
# @app.get("/ui/get_claim_bundle/{fileid}")
# def get_claim_bundle(fileid: str) -> dict:
#     """Get JSON, audio, and text for a claim for UI review/editing."""
#     try:
#         payload = {"id": fileid}
#         response = requests.post(DOWNLOAD_URL, headers=HEADERS, json=payload)
#         response.raise_for_status()
#         result = response.json()
#         file_name = result.get("fileName", "claim.json")
#         file_content = result.get("fileContent")
#         if not file_content:
#             raise HTTPException(status_code=404, detail="No file content found.")
#         decoded_json = base64.b64decode(file_content).decode("utf-8")
#         # Try to find associated audio/text (assumes naming convention)
#         base_id = fileid.split("_")[0]
#         audio_path = f"/DataScience/akashreddy/AI_Projects/VoiclaimBot/voiclaim_data/cleaned_audio/{fileid}.wav"
#         text_path = f"/DataScience/akashreddy/AI_Projects/VoiclaimBot/voiclaim_data/text_transcriptions/{fileid}.txt"
#         audio_exists = os.path.exists(audio_path)
#         text_exists = os.path.exists(text_path)
#         audio_b64 = None
#         text_content = None
#         if audio_exists:
#             with open(audio_path, "rb") as f:
#                 audio_b64 = base64.b64encode(f.read()).decode("utf-8")
#         if text_exists:
#             with open(text_path, "r") as f:
#                 text_content = f.read()
#         return {
#             "json": decoded_json,
#             "audio_b64": audio_b64,
#             "text": text_content,
#             "fileid": fileid,
#             "file_name": file_name
#         }
#     except Exception as e:
#         logger.error(f"Get claim bundle failed: {e}")
#         raise HTTPException(status_code=500, detail="Get claim bundle failed.")
#
# @app.post("/ui/update_claim_json")
# def update_claim_json(req: UpdateJsonRequest) -> dict:
#     """Accept updated JSON from UI and save it locally."""
#     try:
#         json_str = json.dumps(req.updated_json, indent=2)
#         local_dir = "/DataScience/akashreddy/AI_Projects/VoiclaimBot/voiclaim_data/updated_claims"
#         os.makedirs(local_dir, exist_ok=True)
#         local_path = os.path.join(local_dir, f"{req.fileid}_updated.json")
#         with open(local_path, "w") as f:
#             f.write(json_str)
#         logger.info(f"Updated JSON saved locally for {req.fileid} at {local_path}")
#         return {"status": "success", "fileid": req.fileid, "fileName": f"{req.fileid}_updated.json", "local_path": local_path}
#     except Exception as e:
#         logger.error(f"Update claim JSON failed: {e}")
#         raise HTTPException(status_code=500, detail="Update claim JSON failed.")
#
# @app.get("/health")
# def health() -> dict:
#     return {"status": "ok"}
#
# @app.get("/ui/test_claim_bundle/{fileid}")
# def test_claim_bundle(fileid: str) -> dict:
#     """
#     Test endpoint for UI: returns all claim bundles for the latest main file in processed dir.
#     Groups claim JSONs by main file name (before '_claim').
#     """
#     processed_dir = "/DataScience/akashreddy/AI_Projects/VoiclaimBot/voiclaim_data/claim_output/processed"
#     try:
#         files = [f for f in os.listdir(processed_dir) if f.endswith(".json")]
#         if not files:
#             return {"error": "No processed claim files found."}
#         # Group files by main file name (before '_claim')
#         from collections import defaultdict
#         import re
#
#         grouped = defaultdict(list)
#         for fname in files:
#             match = re.match(r"(.+?)(_claim\d+)?\.json$", fname)
#             if match:
#                 main_name = match.group(1)
#                 grouped[main_name].append(fname)
#
#         # Find the latest main file group by modification time of any of its claim files
#         latest_main = None
#         latest_mtime = 0
#         for main_name, claim_files in grouped.items():
#             mtimes = [os.path.getmtime(os.path.join(processed_dir, f)) for f in claim_files]
#             max_mtime = max(mtimes)
#             if max_mtime > latest_mtime:
#                 latest_mtime = max_mtime
#                 latest_main = main_name
#
#         if not latest_main:
#             return {"error": "No valid claim groups found."}
#
#         # Collect all claim JSONs for the latest main file
#         claim_jsons = []
#         for fname in grouped[latest_main]:
#             json_path = os.path.join(processed_dir, fname)
#             with open(json_path, "r") as f:
#                 claim_jsons.append({
#                     "file_name": fname,
#                     "json": f.read()
#                 })
#
#         # Try to find associated audio/text
#         audio_path = f"/DataScience/akashreddy/AI_Projects/VoiclaimBot/voiclaim_data/cleaned_audio/{latest_main}.wav"
#         text_path = f"/DataScience/akashreddy/AI_Projects/VoiclaimBot/voiclaim_data/text_transcriptions/{latest_main}.txt"
#         audio_b64 = None
#         text_content = None
#         if os.path.exists(audio_path):
#             with open(audio_path, "rb") as af:
#                 audio_b64 = base64.b64encode(af.read()).decode("utf-8")
#         if os.path.exists(text_path):
#             with open(text_path, "r") as tf:
#                 text_content = tf.read()
#         return {
#             "main_file": latest_main,
#             "claims": claim_jsons,
#             "audio_b64": audio_b64,
#             "text": text_content,
#             "fileid": latest_main
#         }
#     except Exception as e:
#         logger.error(f"Failed to get latest claim bundle: {e}")
#         return {"error": "Failed to get latest claim bundle."}
#
# # -------------------------------
# # Testing Guide (Manual API Tests)
# # -------------------------------
# # 1. Start the server:
# #    uvicorn scripts/api_server:app --reload
# #
# # 2. Test health endpoint:
# #    curl http://localhost:8000/health
# #
# # 3. Test UI test claim bundle:
# #    curl http://localhost:8000/ui/test_claim_bundle/123
# #
# # 4. Test get_claim_bundle (replace <fileid>):
# #    curl http://localhost:8000/ui/get_claim_bundle/<fileid>
# #
# # 5. Test update_claim_json (save updated JSON locally):
# #    http POST http://localhost:8000/ui/update_claim_json fileid="123" updated_json:='{"foo": "bar"}'
# #    # or using curl:
# #    curl -X POST http://localhost:8000/ui/update_claim_json \
# #      -H "Content-Type: application/json" \
# #      -d '{"fileid": "123", "updated_json": {"foo": "bar"}}'
# #
# # 6. For file upload/download, use the /blob/upload and /blob/download endpoints.
# #
# # 7. You can also use the interactive docs at:
# #    http://localhost:8000/docs
