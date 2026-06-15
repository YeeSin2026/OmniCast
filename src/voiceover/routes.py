"""VoiceOver 路由 — 声音样本管理 + TTS 合成 + 音频服务。

复用 social_agent/routes.py 的 HTMX 轮询 + 后台线程模式。
"""

import asyncio
import json
import logging
import os
import threading
import uuid

from fastapi import Form, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse

from .. import settings as config
from . import client as tts_client
from . import speaker as speaker_mgr

logger = logging.getLogger(__name__)

# 后台 TTS 任务跟踪
_JOBS: dict[str, dict] = {}

def _sanitize_for_tts(text: str, max_chars: int = 250) -> str:
    """清洗文案使其更适合 TTS 朗读。

    移除 emoji、Markdown 标记、多余空行，保留中文标点和自然停顿。
    超过 max_chars 的文本会被截断（确保 CosyVoice 不会因过长文本崩溃）。
    """
    import re
    # 移除 emoji 和特殊符号
    text = re.sub(r'[\U0001F300-\U0001F9FF☀-➿⭐✀-➿]', '', text)
    # 移除 Markdown 加粗/斜体
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
    # 移除 [画面：xxx] 等视频导演标注
    text = re.sub(r'\[画面[：:][^\]]+\]', '', text)
    text = re.sub(r'\[停[^\]]*\]', '', text)
    # 合并多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    # 截断过长文本（CosyVoice 生成超长语音可能 OOM）
    if len(text) > max_chars:
        original_len = len(text)
        # 在最后一个完整句号/问号/感叹号处截断
        truncated = text[:max_chars]
        last_end = max(truncated.rfind('。'), truncated.rfind('？'), truncated.rfind('！'), truncated.rfind('\n'))
        if last_end > max_chars // 2:
            text = truncated[:last_end + 1]
        else:
            text = truncated
        logger.warning("TTS 文本过长，已截断: %d → %d 字", original_len, len(text))

    return text


def register_routes(app, jinja_env, render_func):
    """注册 VoiceOver 相关路由到 FastAPI 应用。"""

    # ═══════════════════════════════════════════
    #  声音样本管理页面
    # ═══════════════════════════════════════════

    @app.get("/agent/speaker", response_class=HTMLResponse)
    async def speaker_page(request: Request):
        """声音样本管理页面 — 上传你的参考音频。"""
        status = speaker_mgr.get_speaker_status()
        return HTMLResponse(render_func(
            "speaker_setup.html",
            request=request,
            speaker=status,
        ))

    @app.get("/api/agent/speaker/status", response_class=HTMLResponse)
    async def speaker_status():
        """返回声音配置状态 HTMX 片段。"""
        status = speaker_mgr.get_speaker_status()
        return HTMLResponse(render_func(
            "speaker_status.html",
            speaker=status,
        ))

    @app.post("/api/agent/speaker/upload")
    async def upload_reference_audio(
        audio: UploadFile = File(...),
        prompt_text: str = Form(""),
    ):
        """上传参考音频 — 这是声音克隆的核心步骤。

        上传一段 15-60 秒的清晰录音，CosyVoice 将基于此复刻你的声音。
        """
        # 校验文件格式
        if not audio.filename:
            return HTMLResponse(
                '<div class="text-red-500 dark:text-red-400 text-[13px]">未选择文件</div>'
            )

        suffix = os.path.splitext(audio.filename)[1].lower()
        if suffix not in (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".webm"):
            return HTMLResponse(
                '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                f'不支持的格式 "{suffix}"，请上传 WAV/MP3/M4A/WebM 音频文件</div>'
            )

        contents = await audio.read()
        if len(contents) < 2048:
            return HTMLResponse(
                '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                '音频文件过短（少于 1 秒），请录制至少 15 秒的清晰语音</div>'
            )

        # ── 音频预处理：格式转换 + 截断 ──
        import subprocess, tempfile
        suffix = os.path.splitext(audio.filename)[1].lower() or ".wav"
        filename_base = os.path.splitext(audio.filename)[0]

        # Step 1: 非 WAV 格式转 WAV（CosyVoice 的 soundfile 后端兼容性最好）
        if suffix in (".webm", ".ogg", ".flac", ".aac", ".mp3", ".m4a"):
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(contents)
                raw_path = f.name
            wav_path = raw_path + "_converted.wav"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", raw_path, "-ar", "16000", "-ac", "1",
                     "-sample_fmt", "s16", wav_path],
                    capture_output=True, timeout=30, check=True,
                )
                with open(wav_path, "rb") as f:
                    contents = f.read()
                suffix = ".wav"
                logger.info("格式转换完成: %d bytes WAV", len(contents))
            except Exception as e:
                logger.error("格式转换失败: %s", e)
                return HTMLResponse(
                    '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                    '音频格式转换失败，请尝试直接上传 WAV 文件</div>')
            finally:
                for p in (raw_path, wav_path):
                    if os.path.exists(p):
                        os.unlink(p)

        # Step 2: 截取前 20 秒（CosyVoice 编码器输入上限约 30 秒）
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(contents)
            raw_path = f.name
        trimmed_path = raw_path + "_trimmed.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", raw_path, "-t", "20", "-ar", "16000",
                 "-ac", "1", "-sample_fmt", "s16", trimmed_path],
                capture_output=True, timeout=30, check=True,
            )
            with open(trimmed_path, "rb") as f:
                trimmed = f.read()
            if len(trimmed) < len(contents):
                logger.info("音频已截断至前20秒: %d → %d bytes", len(contents), len(trimmed))
                contents = trimmed
        except Exception as e:
            logger.warning("音频截断跳过: %s", e)
        finally:
            for p in (raw_path, trimmed_path):
                if os.path.exists(p):
                    os.unlink(p)

        saved_path = speaker_mgr.save_reference_audio(
            contents, prompt_text, f"{filename_base}{suffix}"
        )

        if not saved_path:
            return HTMLResponse(
                '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                '保存失败，请检查磁盘空间后重试</div>'
            )

        status = speaker_mgr.get_speaker_status()
        logger.info("声音样本上传成功: %s", saved_path)
        return HTMLResponse(render_func("speaker_status.html", speaker=status, just_saved=True))

    # ═══════════════════════════════════════════
    #  TTS 合成（草稿 → 口播音频）
    # ═══════════════════════════════════════════

    @app.post("/api/agent/draft/{draft_id}/tts")
    async def start_tts_synthesis(draft_id: int):
        """为指定草稿启动 TTS 合成任务。

        返回 HTMX 轮询片段，前端每 2 秒查询进度。
        """
        # Lazy import 避免循环依赖
        from ..social_agent.store import DraftStore
        store = DraftStore()

        draft = store.get_by_id(draft_id)
        if not draft:
            raise HTTPException(404, "草稿未找到")

        content_text = draft.get("content_text", "").strip()
        if not content_text or len(content_text) < 10:
            return HTMLResponse(
                '<div id="tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                '草稿内容过短（少于 10 字符），无法生成音频</div>'
            )

        if not speaker_mgr.has_reference_audio():
            return HTMLResponse(
                '<div id="tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                '尚未配置参考音频，请先 <a href="/agent/speaker" '
                'class="underline hover:text-red-600">上传你的声音样本</a></div>'
            )

        # 检查是否已有音频，避免重复生成
        try:
            meta = json.loads(draft.get("metadata_json", "{}"))
            existing_audio = meta.get("audio_path", "")
            if existing_audio:
                existing_full = os.path.join(config.AUDIO_OUTPUT_DIR, existing_audio)
                if os.path.exists(existing_full):
                    return HTMLResponse(
                        f'<div id="tts-status" class="flex items-center gap-3">'
                        f'<audio controls preload="metadata" class="h-10 w-64">'
                        f'<source src="/api/agent/draft/{draft_id}/audio/{existing_audio}" type="audio/wav">'
                        f'</audio>'
                        f'<span class="text-[11px] text-zinc-400">已有音频</span>'
                        f'<button hx-post="/api/agent/draft/{draft_id}/tts" '
                        f'hx-target="#tts-status" hx-swap="outerHTML" '
                        f'class="text-[11px] text-zinc-500 hover:text-zinc-700 underline">重新生成</button>'
                        f'</div>'
                    )
        except (json.JSONDecodeError, TypeError):
            pass

        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "status": "pending",
            "result": {},
            "draft_id": draft_id,
        }

        # 后台线程运行 TTS（避免阻塞 web 请求）
        thread = threading.Thread(
            target=_run_tts_synthesis,
            args=(job_id, draft_id, content_text),
            daemon=True,
        )
        thread.start()

        logger.info("TTS 任务已启动: job=%s draft=%d text_len=%d", job_id, draft_id, len(content_text))

        # 返回 HTMX 轮询片段
        return HTMLResponse(
            f'<div id="tts-status" hx-get="/api/agent/draft/{draft_id}/tts/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 '
            f'border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin mr-2 align-middle"></span>'
            f'正在生成口播音频…（{len(content_text)} 字文案）</span></div>'
        )

    @app.get("/api/agent/draft/{draft_id}/tts/status/{job_id}")
    async def poll_tts_status(draft_id: int, job_id: str):
        """轮询 TTS 任务状态，返回 HTMX 片段。"""
        job = _JOBS.get(job_id)
        if not job:
            return HTMLResponse(
                f'<div id="tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                '任务已过期或未找到</div>'
            )

        if job["status"] == "done":
            audio_filename = job["result"].get("audio_filename", "")
            duration = job["result"].get("duration_sec", 0)

            if not audio_filename:
                return HTMLResponse(
                    f'<div id="tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                    '音频生成异常：未获取到文件名</div>'
                )

            return HTMLResponse(
                f'<div id="tts-status" class="flex items-center gap-3 flex-wrap">'
                f'<audio controls preload="metadata" class="h-10 max-w-[320px]">'
                f'<source src="/api/agent/draft/{draft_id}/audio/{audio_filename}" type="audio/wav">'
                f'</audio>'
                f'<span class="text-[11px] text-emerald-600 dark:text-emerald-400">'
                f'口播音频已生成 · {duration:.0f}秒</span>'
                f'<button hx-post="/api/agent/draft/{draft_id}/tts" '
                f'hx-target="#tts-status" hx-swap="outerHTML" '
                f'class="text-[11px] text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 underline">'
                f'重新生成</button>'
                f'<button hx-delete="/api/agent/draft/{draft_id}/audio" '
                f'hx-target="#tts-section" hx-swap="outerHTML" '
                f'hx-confirm="确定删除这条口播音频吗？" '
                f'class="text-[11px] text-red-400 hover:text-red-500 dark:text-red-500 dark:hover:text-red-400 underline">'
                f'删除</button>'
                f'</div>'
            )

        elif job["status"] == "failed":
            error = job["result"].get("error", "未知错误")
            return HTMLResponse(
                f'<div id="tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                f'音频生成失败: {error[:300]}</div>'
            )

        else:
            # 仍在处理中 — 继续轮询
            return HTMLResponse(
                f'<div id="tts-status" hx-get="/api/agent/draft/{draft_id}/tts/status/{job_id}" '
                f'hx-trigger="every 2s" hx-swap="outerHTML">'
                f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
                f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 '
                f'border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin mr-2 align-middle"></span>'
                f'正在生成口播音频…</span></div>'
            )

    @app.get("/api/agent/draft/{draft_id}/audio/{filename}")
    async def serve_audio_file(draft_id: int, filename: str):
        """提供生成的音频文件下载/播放。

        安全校验：防止路径遍历攻击。
        """
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(400, "文件名不合法")

        filepath = os.path.join(config.AUDIO_OUTPUT_DIR, filename)
        if not os.path.exists(filepath):
            raise HTTPException(404, "音频文件未找到，可能已被清理")

        return FileResponse(
            filepath,
            media_type="audio/wav",
            headers={"Accept-Ranges": "bytes"},
        )

    @app.delete("/api/agent/draft/{draft_id}/audio")
    async def delete_audio_file(draft_id: int):
        """删除草稿关联的口播音频文件。"""
        from ..social_agent.store import DraftStore
        store = DraftStore()
        draft = store.get_by_id(draft_id)
        if not draft:
            raise HTTPException(404, "草稿未找到")

        # 从 metadata 中获取音频路径并删除文件
        meta = {}
        try:
            meta = json.loads(draft.get("metadata_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            pass

        audio_filename = meta.pop("audio_path", None)
        meta.pop("audio_duration_sec", None)
        if audio_filename:
            filepath = os.path.join(config.AUDIO_OUTPUT_DIR, audio_filename)
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info("音频文件已删除: %s", filepath)

        # 更新草稿 metadata
        conn = store._get_conn()
        try:
            conn.execute(
                "UPDATE content_drafts SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False),
                 __import__("datetime").datetime.now().isoformat(),
                 draft_id),
            )
            conn.commit()
        finally:
            conn.close()

        # 返回空的 TTS 面板
        speaker_ok = speaker_mgr.has_reference_audio()
        return HTMLResponse(render_func(
            "tts_panel.html",
            draft_id=draft_id,
            has_audio=False,
            audio_filename="",
            speaker_configured=speaker_ok,
            draft_platform=draft.get("platform", ""),
            draft_text_len=len(draft.get("content_text", "")),
        ))

    @app.get("/api/agent/draft/{draft_id}/tts-panel", response_class=HTMLResponse)
    async def tts_panel_fragment(draft_id: int):
        """返回草稿页面的 TTS 面板 HTMX 片段。

        此端点被 agent_detail.html 通过 hx-trigger="load" 懒加载。
        """
        from ..social_agent.store import DraftStore
        store = DraftStore()

        draft = store.get_by_id(draft_id)
        if not draft:
            return HTMLResponse(
                '<div class="text-[13px] text-zinc-400">草稿未找到</div>'
            )

        # 检查是否已有音频
        has_audio = False
        audio_filename = ""
        try:
            meta = json.loads(draft.get("metadata_json", "{}"))
            af = meta.get("audio_path", "")
            if af:
                full = os.path.join(config.AUDIO_OUTPUT_DIR, af)
                if os.path.exists(full):
                    has_audio = True
                    audio_filename = af
        except (json.JSONDecodeError, TypeError):
            pass

        speaker_ok = speaker_mgr.has_reference_audio()

        return HTMLResponse(render_func(
            "tts_panel.html",
            draft_id=draft_id,
            has_audio=has_audio,
            audio_filename=audio_filename,
            speaker_configured=speaker_ok,
            draft_platform=draft.get("platform", ""),
            draft_text_len=len(draft.get("content_text", "")),
        ))

    # ═══════════════════════════════════════════
    #  自由文本转语音（上传文档 / 粘贴文本）
    # ═══════════════════════════════════════════

    @app.post("/api/agent/speaker/tts")
    async def speaker_tts(
        text_content: str = Form(""),
        text_file: UploadFile | None = None,
    ):
        """自由文本转语音 — 上传 txt/md 文档或粘贴文本，用克隆声音生成音频。

        不依赖 OmniCast 草稿，用户可以用自己的文案生成口播音频。
        """
        # 获取文本内容
        text = text_content.strip()

        if text_file and text_file.filename:
            # 校验文件类型
            suffix = os.path.splitext(text_file.filename)[1].lower()
            if suffix not in (".txt", ".md", ".text", ".markdown"):
                return HTMLResponse(
                    '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                    f'不支持的格式 "{suffix}"，请上传 .txt 或 .md 文件</div>'
                )
            try:
                file_bytes = await text_file.read()
                # 尝试 UTF-8 解码
                file_text = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    file_text = file_bytes.decode("gbk")
                except Exception:
                    return HTMLResponse(
                        '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                        '文件编码无法识别，请使用 UTF-8 编码的文本文件</div>'
                    )
            if not text:
                text = file_text.strip()

        if not text or len(text) < 5:
            return HTMLResponse(
                '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                '文本内容过短（少于 5 字符），无法生成音频</div>'
            )

        if not speaker_mgr.has_reference_audio():
            return HTMLResponse(
                '<div class="text-red-500 dark:text-red-400 text-[13px]">'
                '尚未配置声音样本，请先上传你的录音。</div>'
            )

        # 清洗文本
        clean_text = _sanitize_for_tts(text, max_chars=2000)

        job_id = uuid.uuid4().hex[:12]
        _JOBS[job_id] = {
            "status": "pending",
            "result": {},
            "text_len": len(clean_text),
        }

        thread = threading.Thread(
            target=_run_speaker_tts,
            args=(job_id, clean_text),
            daemon=True,
        )
        thread.start()

        logger.info("自由文本 TTS 任务已启动: job=%s text_len=%d", job_id, len(clean_text))

        return HTMLResponse(
            f'<div id="speaker-tts-status" hx-get="/api/agent/speaker/tts/status/{job_id}" '
            f'hx-trigger="every 2s" hx-swap="outerHTML">'
            f'<div class="flex items-center gap-2">'
            f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 '
            f'border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin"></span>'
            f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
            f'正在生成口播音频…（{len(clean_text)} 字）</span>'
            f'</div></div>'
        )

    @app.get("/api/agent/speaker/tts/status/{job_id}")
    async def speaker_tts_status(job_id: str):
        """轮询自由文本 TTS 任务状态。"""
        job = _JOBS.get(job_id)
        if not job:
            return HTMLResponse(
                '<div id="speaker-tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                '任务已过期或未找到</div>'
            )

        if job["status"] == "done":
            audio_filename = job["result"].get("audio_filename", "")
            duration = job["result"].get("duration_sec", 0)
            out_path = job["result"].get("audio_path", "")

            if not audio_filename:
                return HTMLResponse(
                    '<div id="speaker-tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                    '音频生成异常</div>'
                )

            return HTMLResponse(
                f'<div id="speaker-tts-status" class="space-y-3">'
                f'<audio controls preload="metadata" class="h-10 w-full max-w-[400px]">'
                f'<source src="/api/agent/speaker/audio/{audio_filename}" type="audio/wav">'
                f'</audio>'
                f'<div class="flex items-center gap-3 flex-wrap">'
                f'<span class="text-[11px] text-emerald-600 dark:text-emerald-400">'
                f'口播音频已生成 · {duration:.0f}秒</span>'
                f'<a href="/api/agent/speaker/audio/{audio_filename}" download '
                f'class="text-[11px] px-3 py-1.5 bg-zinc-100 dark:bg-zinc-800 text-zinc-500 '
                f'dark:text-zinc-400 rounded-lg hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors">'
                f'📥 下载音频</a>'
                f'</div>'
                f'</div>'
            )

        elif job["status"] == "failed":
            error = job["result"].get("error", "未知错误")
            return HTMLResponse(
                f'<div id="speaker-tts-status" class="text-red-500 dark:text-red-400 text-[13px]">'
                f'音频生成失败: {error[:300]}</div>'
            )

        else:
            text_len = job.get("text_len", 0)
            return HTMLResponse(
                f'<div id="speaker-tts-status" hx-get="/api/agent/speaker/tts/status/{job_id}" '
                f'hx-trigger="every 2s" hx-swap="outerHTML">'
                f'<div class="flex items-center gap-2">'
                f'<span class="inline-block w-4 h-4 border-2 border-zinc-300 dark:border-zinc-600 '
                f'border-t-zinc-500 dark:border-t-zinc-400 rounded-full animate-spin"></span>'
                f'<span class="text-[13px] text-zinc-500 dark:text-zinc-400">'
                f'正在生成口播音频…{f"（{text_len} 字）" if text_len else ""}</span>'
                f'</div></div>'
            )

    @app.get("/api/agent/speaker/audio/{filename}")
    async def serve_speaker_audio(filename: str):
        """提供自由文本 TTS 生成的音频文件。"""
        if ".." in filename or "/" in filename or "\\" in filename:
            raise HTTPException(400, "文件名不合法")

        filepath = os.path.join(config.AUDIO_OUTPUT_DIR, filename)
        if not os.path.exists(filepath):
            raise HTTPException(404, "音频文件未找到，可能已被清理")

        return FileResponse(
            filepath,
            media_type="audio/wav",
            headers={"Accept-Ranges": "bytes"},
        )

    logger.info("VoiceOver 路由已注册（10 个端点）")


# ═══════════════════════════════════════════
#  后台 TTS 合成任务
# ═══════════════════════════════════════════

def _run_tts_synthesis(job_id: str, draft_id: int, content_text: str):
    """在后台线程中调用 VoxCPM 合成音频，保存到磁盘，更新草稿元数据。

    复用 _run_generation() 的线程 + asyncio 模式。
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        wav_path = speaker_mgr.get_reference_wav_path()
        prompt_text = speaker_mgr.get_prompt_text()

        if not wav_path:
            raise RuntimeError("参考音频未配置")

        # 清洗文案，去掉 emoji 和导演标注
        clean_text = _sanitize_for_tts(content_text)

        logger.info(
            "TTS job=%s: 开始合成 draft #%d (原文 %d 字, 清洗后 %d 字)",
            job_id, draft_id, len(content_text), len(clean_text),
        )

        # 调用 VoxCPM（返回完整 WAV 字节）
        wav_data = loop.run_until_complete(
            tts_client.synthesize_speech(
                tts_text=clean_text,
                prompt_text=prompt_text,
                prompt_wav_path=wav_path,
            )
        )

        if not wav_data or len(wav_data) < 500:
            raise RuntimeError(
                f"VoxCPM 返回音频异常: {len(wav_data) if wav_data else 0} bytes"
            )

        # 保存到磁盘（VoxCPM 直接返回 WAV，无需转换）
        os.makedirs(config.AUDIO_OUTPUT_DIR, exist_ok=True)
        out_filename = f"draft_{draft_id}_{uuid.uuid4().hex[:8]}.wav"
        out_path = os.path.join(config.AUDIO_OUTPUT_DIR, out_filename)
        with open(out_path, "wb") as f:
            f.write(wav_data)

        # 估算时长：WAV 文件大小 / (采样率 * 位深 * 声道)
        # VoxCPM 默认 44100Hz 16-bit mono → 88200 bytes/s
        duration = len(wav_data) / 88200  # rough estimate from WAV size
        logger.info(
            "TTS job=%s: 音频已保存 %s (~%.1fs, %d bytes)",
            job_id, out_path, duration, len(wav_data),
        )

        # 更新草稿 metadata_json
        from ..social_agent.store import DraftStore
        store = DraftStore()
        draft = store.get_by_id(draft_id)
        if draft:
            try:
                meta = json.loads(draft.get("metadata_json", "{}"))
            except (json.JSONDecodeError, TypeError):
                meta = {}
            meta["audio_path"] = out_filename
            meta["audio_duration_sec"] = round(duration, 1)

            # 直接 UPDATE metadata_json 字段
            conn = store._get_conn()
            try:
                conn.execute(
                    "UPDATE content_drafts SET metadata_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(meta, ensure_ascii=False),
                     __import__("datetime").datetime.now().isoformat(),
                     draft_id),
                )
                conn.commit()
            finally:
                conn.close()

        _JOBS[job_id] = {
            "status": "done",
            "result": {
                "audio_filename": out_filename,
                "audio_path": out_path,
                "duration_sec": duration,
                "draft_id": draft_id,
            },
        }

    except Exception as e:
        logger.error("TTS job=%s 失败: %s", job_id, e, exc_info=True)
        _JOBS[job_id] = {
            "status": "failed",
            "result": {"error": str(e)[:500]},
        }
    finally:
        loop.close()


def _run_speaker_tts(job_id: str, clean_text: str):
    """自由文本 TTS 后台任务 — 与草稿无关，直接合成音频。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        wav_path = speaker_mgr.get_reference_wav_path()
        prompt_text = speaker_mgr.get_prompt_text()

        if not wav_path:
            raise RuntimeError("参考音频未配置")

        logger.info("自由文本 TTS job=%s: 开始合成 (%d 字)", job_id, len(clean_text))

        wav_data = loop.run_until_complete(
            tts_client.synthesize_speech(
                tts_text=clean_text,
                prompt_text=prompt_text,
                prompt_wav_path=wav_path,
            )
        )

        if not wav_data or len(wav_data) < 500:
            raise RuntimeError(
                f"VoxCPM 返回音频异常: {len(wav_data) if wav_data else 0} bytes"
            )

        # 保存到磁盘
        os.makedirs(config.AUDIO_OUTPUT_DIR, exist_ok=True)
        out_filename = f"speaker_{uuid.uuid4().hex[:8]}.wav"
        out_path = os.path.join(config.AUDIO_OUTPUT_DIR, out_filename)
        with open(out_path, "wb") as f:
            f.write(wav_data)

        duration = len(wav_data) / 88200
        logger.info(
            "自由文本 TTS job=%s: 音频已保存 %s (~%.1fs, %d bytes)",
            job_id, out_path, duration, len(wav_data),
        )

        _JOBS[job_id] = {
            "status": "done",
            "result": {
                "audio_filename": out_filename,
                "audio_path": out_path,
                "duration_sec": duration,
            },
        }

    except Exception as e:
        logger.error("自由文本 TTS job=%s 失败: %s", job_id, e, exc_info=True)
        _JOBS[job_id] = {
            "status": "failed",
            "result": {"error": str(e)[:500]},
        }
    finally:
        loop.close()
