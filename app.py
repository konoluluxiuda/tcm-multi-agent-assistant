import os
from contextlib import asynccontextmanager
import uuid
import tempfile
from typing import Dict, Union, Optional, List
import glob
import threading
import time
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Request, Response, Cookie
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, Response as BinaryResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import uvicorn

from config import Config
from agents.agent_decision import process_query
from agents import agent_decision as agent_runtime
from agents.conversation_store import MemoryUnavailable, ConversationConflict, SessionExpired
from agents.runtime import session_turn
from features import env_bool, session_cookie_name
from tcm.chat import execute_turn, parse_request as parse_herb_request
from tcm.agent_research import split_contexts, runtime as research_runtime, parse_request as parse_research_request
from tcm.mineru_research import SnapshotChanged
from source_access import SourceCatalog, PrivateArtifacts, create_source_router

# Load configuration
config = Config()
SESSION_COOKIE_NAME = session_cookie_name()

@asynccontextmanager
async def lifespan(application):
    # Validate storage and purge expired payloads before accepting requests.
    agent_runtime.memory.purge_expired()
    yield


# Initialize FastAPI app
app = FastAPI(title="Multi-Agent Medical Chatbot", version="2.0", lifespan=lifespan)

# Set up directories
UPLOAD_FOLDER = "uploads/backend"
FRONTEND_UPLOAD_FOLDER = "uploads/frontend"
SKIN_LESION_OUTPUT = "uploads/skin_lesion_output"
SPEECH_DIR = "uploads/speech"

# Create directories if they don't exist
for directory in [UPLOAD_FOLDER, FRONTEND_UPLOAD_FOLDER, SKIN_LESION_OUTPUT, SPEECH_DIR]:
    os.makedirs(directory, exist_ok=True)

# Public sources are explicitly registered; private results are session snapshots.
source_catalog = SourceCatalog(Path.cwd(), os.getenv('SOURCE_MANIFEST_PATH', 'resources/source_manifest.json'))
private_artifacts = PrivateArtifacts()
app.include_router(create_source_router(source_catalog, private_artifacts, cookie_name=SESSION_COOKIE_NAME))
image_request_lock = threading.Lock()


def process_image_query(query, session_id):
    # The legacy CV backend writes a shared PNG. Serialize generation + snapshot
    # and discard stale output before starting a new request.
    path = Path(SKIN_LESION_OUTPUT) / 'segmentation_plot.png'
    with image_request_lock:
        path.unlink(missing_ok=True)
        result = process_query(query, session_id=session_id)
        artifact_url = None
        if result['agent_name'] == 'SKIN_LESION_AGENT, HUMAN_VALIDATION' and path.is_file():
            artifact_url = private_artifacts.publish_png(path.read_bytes(), session_id)
        return result, artifact_url

# Set up templates
templates = Jinja2Templates(directory="templates")

# Optional speech SDK is loaded only on an enabled transcription request.
client = None
client_lock = threading.Lock()


def get_speech_client():
    if not config.features.state("speech")["available"]:
        raise ValueError("Speech is disabled or missing credentials")
    global client
    with client_lock:
        if client is None:
            from elevenlabs.client import ElevenLabs
            client = ElevenLabs(api_key=config.speech.eleven_labs_api_key)
    return client


def feature_error(name):
    state = config.features.state(name)
    if state['available']:
        return None
    return JSONResponse(status_code=403 if state['reason'] == 'disabled' else 503,
                        content={'status': 'feature_disabled' if state['reason'] == 'disabled' else 'feature_unavailable',
                                 'feature': name, 'reason': state['reason']})


@app.middleware('http')
async def enforce_features(request: Request, call_next):
    if (env_bool('RESEARCH_TOOLS_ONLY') and request.method in ('POST', 'PUT', 'PATCH', 'DELETE')
            and request.url.path not in ('/chat', '/conversation')):
        return JSONResponse(status_code=403, content={'detail': '此部署仅支持药材与药典研究工具及会话管理。'})
    # Check before parsing upload bodies or invoking optional clients.
    feature = {'/upload': 'image_analysis', '/transcribe': 'speech', '/generate-speech': 'speech'}.get(request.url.path)
    if request.method == 'POST' and feature:
        error = feature_error(feature)
        if error is not None:
            return error
    return await call_next(request)


@app.get('/features')
def features():
    return config.features.as_dict()

# Define allowed file extensions
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    """Check if file has an allowed extension"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def cleanup_old_audio():
    """Deletes all .mp3 files in the uploads/speech folder every 5 minutes."""
    while True:
        try:
            files = glob.glob(f"{SPEECH_DIR}/*.mp3")
            for file in files:
                os.remove(file)
            print("Cleaned up old speech files.")
        except Exception as e:
            print(f"Error during cleanup: {e}")
        time.sleep(300)  # Runs every 5 minutes

# Start background cleanup thread
cleanup_thread = threading.Thread(target=cleanup_old_audio, daemon=True)
if config.features.state("speech")["available"]:
    cleanup_thread.start()

class QueryRequest(BaseModel):
    query: str
    conversation_history: List = []

class SpeechRequest(BaseModel):
    text: str
    voice_id: str = "EXAMPLE_VOICE_ID"  # Default voice ID

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the main HTML page"""
    return templates.TemplateResponse(request=request, name="index.html", context={
        "features": config.features.as_dict(), "research_tools_only": env_bool('RESEARCH_TOOLS_ONLY')})

def session_cookie(value):
    store = agent_runtime.memory
    if store.valid_cookie(value):
        try:
            store.load(value)
            return value
        except SessionExpired:
            pass
    return store.new_cookie()


def set_session_cookie(response, value):
    response.set_cookie(key=SESSION_COOKIE_NAME, value=value, httponly=True, samesite="lax",
                        max_age=agent_runtime.memory.ttl_seconds, secure=env_bool('CHAT_COOKIE_SECURE'))


@app.exception_handler(MemoryUnavailable)
async def memory_unavailable(request, exc):
    return JSONResponse(status_code=503, content={"detail": "会话存储暂不可用，未切换到临时记忆。请检查存储配置。"})


@app.exception_handler(ConversationConflict)
async def memory_conflict(request, exc):
    return JSONResponse(status_code=409, content={"detail": "会话已被其他请求更新，请刷新后重试。"})


@app.exception_handler(SessionExpired)
async def memory_expired(request, exc):
    return JSONResponse(status_code=410, content={"detail": "会话已清除或过期，请刷新后重新开始。"})


@app.get('/conversation')
def conversation(response: Response, session_id: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)):
    session_id = session_cookie(session_id)
    with session_turn(session_id):
        snapshot = agent_runtime.memory.load(session_id)
        result = {"messages": [{"role": "user" if m.type == "human" else "assistant", "content": m.content}
                               for m in snapshot.messages if m.type in ('human', 'ai')],
                  "retention_days": agent_runtime.memory.ttl_seconds / 86400}
        herb_context, research_context = split_contexts(snapshot.tcm_context)
        if herb_context:
            result['tcm'], _ = execute_turn({'followup': True, 'field': 'source'}, herb_context)
        if research_context:
            result['research'], _ = research_runtime.execute({'question': '出处呢'}, research_context)
    set_session_cookie(response, session_id)
    response.headers['Cache-Control'] = 'private, no-store'
    return result


@app.delete('/conversation')
def clear_conversation(response: Response, session_id: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)):
    if agent_runtime.memory.valid_cookie(session_id):
        with session_turn(session_id):
            agent_runtime.memory.delete(session_id)
    set_session_cookie(response, agent_runtime.memory.new_cookie())
    response.headers['Cache-Control'] = 'private, no-store'
    return {"status": "cleared"}


@app.get("/health")
def health_check():
    """Health check endpoint for Docker health checks"""
    return {"status": "healthy"}


@app.get('/ready/research')
def research_ready():
    import sqlite3
    try:
        service = research_runtime.get_service()
        if service is None:
            return JSONResponse(status_code=503, content={'status': 'not_ready', 'reason': 'research_disabled'})
        service.check()
        return {'status': 'ready', 'research_only': True, 'human_signoff': False,
                'index_sha256': service.receipt['index_sha256']}
    except (SnapshotChanged, OSError, ValueError, sqlite3.Error, KeyError, TypeError):
        return JSONResponse(status_code=503, content={'status': 'not_ready', 'reason': 'research_unavailable'})


@app.get('/research-evidence.js')
def research_evidence_script():
    return FileResponse(Path(__file__).parent / 'tcm/mineru_evidence.js', media_type='text/javascript')


@app.get('/api/research/page')
def research_page(request: Request, chunk: str, kind: str = 'evidence', index: int = 0):
    pairs = list(request.query_params.multi_items())
    if set(request.query_params) - {'chunk', 'kind', 'index'} or len(pairs) != len(request.query_params):
        return JSONResponse(status_code=400, content={'error': '无效的证据参数。'})
    try:
        service = research_runtime.get_service()
        if service is None:
            return JSONResponse(status_code=503, content={'error': '药典研究工具未启用。'})
        return BinaryResponse(service.preview(chunk, kind, index), media_type='image/png',
                              headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    except SnapshotChanged:
        return JSONResponse(status_code=409, content={'error': '研究来源已改变，请校验并重启服务。'})
    except LookupError:
        return JSONResponse(status_code=404, content={'error': '证据未找到。'})
    except ValueError:
        return JSONResponse(status_code=400, content={'error': '无效的证据参数或来源不匹配。'})
    except (OSError, ImportError):
        return JSONResponse(status_code=503, content={'error': '原页预览暂不可用，请检查PDF依赖及来源。'})


@app.get('/api/research/report')
def research_report(request: Request, q: str, snapshot: str):
    from tcm.mineru_report import export_report
    import sqlite3
    if (set(request.query_params) != {'q', 'snapshot'}
            or len(list(request.query_params.multi_items())) != 2):
        return JSONResponse(status_code=400, content={'error': '无效的导出参数。'})
    try:
        service = research_runtime.get_service()
        if service is None:
            return JSONResponse(status_code=503, content={'error': '药典研究工具未启用。'})
        content = export_report(service, q, snapshot)
        return BinaryResponse(content.encode('utf-8'), media_type='text/markdown; charset=utf-8',
                              headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                       'Content-Disposition': 'attachment; filename="tcm-evidence.md"'})
    except SnapshotChanged:
        return JSONResponse(status_code=409, content={'error': '研究来源或索引版本已改变，请重新检索后再导出。'})
    except ValueError:
        return JSONResponse(status_code=400, content={'error': '请使用当前索引，明确单味药材和字段后再导出。'})
    except (OSError, sqlite3.Error, KeyError, TypeError):
        return JSONResponse(status_code=503, content={'error': '研究证据导出暂不可用，未补造内容。'})

@app.post("/chat")
def chat(
    request: QueryRequest, 
    response: Response, 
    session_id: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Process user text query through the multi-agent system."""
    if (env_bool('RESEARCH_TOOLS_ONLY') and parse_herb_request(request.query) is None
            and parse_research_request(request.query) is None):
        raise HTTPException(status_code=403, detail='此部署仅支持药材资料与药典研究工具；请使用 /herb 或 /research 查询。')
    # Generate session ID for cookie if it doesn't exist
    session_id = session_cookie(session_id)
    
    try:
        response_data = process_query(request.query, session_id=session_id)
        response_text = response_data['messages'][-1].content
        
        # Set session cookie
        set_session_cookie(response, session_id)

        # Check if the agent is skin lesion segmentation and find the image path
        result = {
            "status": "success",
            "response": response_text, 
            "agent": response_data["agent_name"]
        }
        
        if response_data.get("tcm_result") is not None:
            result["tcm"] = response_data["tcm_result"]
            if result["tcm"]["status"] in ("database_unavailable", "dataset_not_found"):
                response.status_code = 503
                result["status"] = "unavailable"
        if response_data.get('research_result') is not None:
            result['research'] = response_data['research_result']
            if result['research']['status'] in ('research_disabled', 'research_unavailable'):
                response.status_code = 503
                result['status'] = 'unavailable'
        return result
    except (MemoryUnavailable, ConversationConflict, SessionExpired):
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/upload")
async def upload_image(
    response: Response,
    image: UploadFile = File(...), 
    text: str = Form(""),
    session_id: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Process medical image uploads with optional text input."""
    from werkzeug.utils import secure_filename
    # Validate file type
    if not allowed_file(image.filename):
        return JSONResponse(
            status_code=400, 
            content={
                "status": "error",
                "agent": "System",
                "response": "Unsupported file type. Allowed formats: PNG, JPG, JPEG"
            }
        )
    
    # Check file size before saving
    file_content = await image.read()
    if len(file_content) > config.api.max_image_upload_size * 1024 * 1024:  # Convert MB to bytes
        return JSONResponse(
            status_code=413, 
            content={
                "status": "error",
                "agent": "System",
                "response": f"File too large. Maximum size allowed: {config.api.max_image_upload_size}MB"
            }
        )
    
    # Generate session ID for cookie if it doesn't exist
    session_id = session_cookie(session_id)
    
    # Save file securely
    filename = secure_filename(f"{uuid.uuid4()}_{image.filename}")
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    with open(file_path, "wb") as f:
        f.write(file_content)
    
    try:
        query = {"text": text, "image": file_path}
        response_data, artifact_url = process_image_query(query, session_id)
        response_text = response_data['messages'][-1].content

        # Set session cookie
        set_session_cookie(response, session_id)

        # Check if the agent is skin lesion segmentation and find the image path
        result = {
            "status": "success",
            "response": response_text, 
            "agent": response_data["agent_name"]
        }
        
        if artifact_url:
            result['result_image'] = artifact_url

        return result
    except (MemoryUnavailable, ConversationConflict, SessionExpired):
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        Path(file_path).unlink(missing_ok=True)

@app.post("/validate")
def validate_medical_output(
    response: Response,
    validation_result: str = Form(...), 
    comments: Optional[str] = Form(None),
    session_id: Optional[str] = Cookie(None, alias=SESSION_COOKIE_NAME)
):
    """Handle human validation for medical AI outputs."""
    # Generate session ID for cookie if it doesn't exist
    session_id = session_cookie(session_id)

    try:
        # Set session cookie
        set_session_cookie(response, session_id)
        
        # Re-run the agent decision system with the validation input
        validation_query = f"Validation result: {validation_result}"
        if comments:
            validation_query += f" Comments: {comments}"
        
        response_data = process_query(validation_query, session_id=session_id)

        if validation_result.lower() == 'yes':
            return {
                "status": "validated",
                "message": "**Output confirmed by human validator:**",
                "response": response_data['messages'][-1].content
            }
        else:
            return {
                "status": "rejected",
                "comments": comments,
                "message": "**Output requires further review:**",
                "response": response_data['messages'][-1].content
            }
    except (MemoryUnavailable, ConversationConflict, SessionExpired):
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """Endpoint to transcribe speech using ElevenLabs API"""
    if not audio.filename:
        return JSONResponse(
            status_code=400,
            content={"error": "No audio file selected"}
        )
    
    try:
        # Save the audio file temporarily
        os.makedirs(SPEECH_DIR, exist_ok=True)
        temp_audio = f"./{SPEECH_DIR}/speech_{uuid.uuid4()}.webm"
        
        # Read and save the file
        audio_content = await audio.read()
        with open(temp_audio, "wb") as f:
            f.write(audio_content)
        
        # Debug: Print file size to check if it's empty
        file_size = os.path.getsize(temp_audio)
        print(f"Received audio file size: {file_size} bytes")
        
        if file_size == 0:
            return JSONResponse(
                status_code=400,
                content={"error": "Received empty audio file"}
            )
        
        # Convert to MP3
        mp3_path = f"./{SPEECH_DIR}/speech_{uuid.uuid4()}.mp3"
        
        try:
            # Use pydub with format detection
            from pydub import AudioSegment
            audio = AudioSegment.from_file(temp_audio)
            audio.export(mp3_path, format="mp3")
            
            # Debug: Print MP3 file size
            mp3_size = os.path.getsize(mp3_path)
            print(f"Converted MP3 file size: {mp3_size} bytes")

            with open(mp3_path, "rb") as mp3_file:
                audio_data = mp3_file.read()
            print(f"Converted audio file into byte array successfully!")

            transcription = get_speech_client().speech_to_text.convert(
                file=audio_data,
                model_id="scribe_v1",
                tag_audio_events=True,
                language_code="eng",
                diarize=True,
            )
            
            # Clean up temp files
            try:
                os.remove(temp_audio)
                os.remove(mp3_path)
                print(f"Deleted temp files: {temp_audio}, {mp3_path}")
            except Exception as e:
                print(f"Could not delete file: {e}")
            
            if transcription.text:
                return {"transcript": transcription.text}
            else:
                return JSONResponse(
                    status_code=500,
                    content={"error": f"API error: {transcription}", "details": transcription.text}
                )

        except Exception as e:
            print(f"Error processing audio: {str(e)}")
            return JSONResponse(
                status_code=500,
                content={"error": f"Error processing audio: {str(e)}"}
            )
                
    except Exception as e:
        print(f"Transcription error: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

@app.post("/generate-speech")
async def generate_speech(request: SpeechRequest):
    """Endpoint to generate speech using ElevenLabs API"""
    try:
        text = request.text
        selected_voice_id = request.voice_id
        
        if not text:
            return JSONResponse(
                status_code=400,
                content={"error": "Text is required"}
            )
        
        # Define API request to ElevenLabs
        elevenlabs_url = f"https://api.elevenlabs.io/v1/text-to-speech/{selected_voice_id}/stream"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": config.speech.eleven_labs_api_key
        }
        payload = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }

        # Send request to ElevenLabs API
        import requests
        response = requests.post(elevenlabs_url, headers=headers, json=payload, timeout=60)

        if response.status_code != 200:
            return JSONResponse(
                status_code=500,
                content={"error": f"Failed to generate speech, status: {response.status_code}", "details": response.text}
            )
        
        # Return directly to the initiating request, without a public disk URL.
        return Response(content=response.content, media_type='audio/mpeg',
                        headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

# Add exception handler for request entity too large
@app.exception_handler(413)
async def request_entity_too_large(request, exc):
    return JSONResponse(
        status_code=413,
        content={
            "status": "error",
            "agent": "System",
            "response": f"File too large. Maximum size allowed: {config.api.max_image_upload_size}MB"
        }
    )

if __name__ == "__main__":
    uvicorn.run(app, host=config.api.host, port=config.api.port)
