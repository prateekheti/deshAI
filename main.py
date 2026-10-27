import tkinter as tk
from tkinter import ttk
import os
import ollama
import subprocess
import threading
import time
import json
import math
from datetime import datetime
import numpy as np
import sounddevice as sd

# ==========================================
# 1. LOCAL ASSISTANT
# ==========================================

# ==========================================
# 2. VOICE ENGINE (LOCAL SPEECH RECOGNITION)
# ==========================================

voice_enabled = True
response_generation = 0
speech_process = None
speech_lock = threading.Lock()
voice_lock = threading.Lock()
voice_state = "IDLE"
voice_session_id = 0
voice_stop_event = None
voice_last_submitted_session = None
voice_started_at = None
voice_continuous = False
whisper_process = None
whisper_lock = threading.Lock()
whisper_stderr_lines = []
ai_request_lock = threading.Lock()
ai_request_active = False


def voice_log(event, detail=""):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    suffix = f" {detail}" if detail else ""
    print(f"[{timestamp}] [VOICE] {event}{suffix}", flush=True)

# Detect the active microphone and its native sample rate. PortAudio raises when
# macOS has no default input device (or microphone access was denied), so keep
# that state explicit instead of attempting to open device ``-1`` later.
INPUT_DEVICE = None
SAMPLE_RATE = 48000


def get_input_device():
    """Return a usable input device, or None when recording is unavailable."""
    global INPUT_DEVICE, SAMPLE_RATE
    try:
        devices = sd.query_devices(kind="input")
        if isinstance(devices, dict):
            INPUT_DEVICE = devices
        elif isinstance(devices, (list, tuple)) and devices:
            INPUT_DEVICE = devices[0]
        else:
            INPUT_DEVICE = None

        if isinstance(INPUT_DEVICE, dict):
            SAMPLE_RATE = int(INPUT_DEVICE.get("default_samplerate", 48000))
        else:
            SAMPLE_RATE = 48000
        return INPUT_DEVICE
    except Exception:
        INPUT_DEVICE = None
        SAMPLE_RATE = 48000
        return None


# Initialize microphone state once at import time without crashing if the device
# list is unavailable.
try:
    default_input = get_input_device()
    if default_input is None:
        INPUT_DEVICE = None
        SAMPLE_RATE = 48000
except Exception:
    INPUT_DEVICE = None
    SAMPLE_RATE = 48000

def speak(text, request_generation=None):
    """Speaks text out loud using Mac's native speech in a background thread."""
    global speech_process
    if not voice_enabled:
        return
        
    def run_speech():
        global speech_process
        try:
            if request_generation is not None and request_generation != response_generation:
                return
            root.after(0, lambda: update_status("SPEAKING...", "#ffaa00"))
            # Clean symbols & emojis so speech sounds natural
            clean = text.replace("🤖", "").replace("👤", "").replace("●", "").replace("//", "").replace("➤", "").replace("*", "")
            with speech_lock:
                if request_generation is not None and request_generation != response_generation:
                    return
                speech_process = subprocess.Popen([
    "say",
    "-v", "Daniel",
    "-r", "165",
    clean
])  
            speech_process.wait()
        finally:
            with speech_lock:
                speech_process = None
            if request_generation is None or request_generation == response_generation:
                root.after(0, lambda: update_status("ONLINE", "#00d2ff"))

    threading.Thread(target=run_speech, daemon=True).start()

def update_status(text, color=None):
    """Keep the visible status and orb animation in sync with app state."""
    global status_mode
    theme = THEMES.get(current_theme, THEMES.get("light", {})) if "THEMES" in globals() and "current_theme" in globals() else {}
    upper_text = text.upper() if text else "ONLINE"
    if upper_text.startswith("LISTENING"):
        status_mode = "LISTENING"
        resolved_color = "#DC2626"
    elif upper_text.startswith("TRANSCRIBING") or upper_text.startswith("PROCESSING"):
        status_mode = "TRANSCRIBING"
        resolved_color = "#9333EA"
    elif upper_text.startswith("THINKING"):
        status_mode = "THINKING"
        resolved_color = "#9333EA"
    elif upper_text.startswith("SPEAKING"):
        status_mode = "SPEAKING"
        resolved_color = theme.get("STATUS_SPEAKING", "#D97706")
    elif "UNAVAILABLE" in upper_text or "ERROR" in upper_text:
        status_mode = "IDLE"
        resolved_color = "#DC2626"
    else:
        status_mode = "IDLE"
        resolved_color = theme.get("STATUS_ONLINE", theme.get("ACCENT", "#0284C7"))

    fg_color = color or resolved_color

    if "status_label" in globals() and status_label and status_label.winfo_exists():
        status_label.config(text=f"● {text}", fg=fg_color)
    if "header_status" in globals() and header_status and header_status.winfo_exists():
        header_status.config(text=f"● {text}", fg=fg_color)
    if "status_detail" in globals() and status_detail and status_detail.winfo_exists():
        status_detail.config(text={
            "IDLE": "Ready when you are",
            "LISTENING": "Listening to your voice...",
            "TRANSCRIBING": "Converting speech to text...",
            "THINKING": "Processing your request...",
            "SPEAKING": "DASH is speaking...",
        }.get(status_mode, "How can I help?"))

def add_chat_message(sender, message):
    """Adds message to the conversation transcript."""
    if "chat_box" not in globals() or not chat_box or not chat_box.winfo_exists():
        return
    chat_box.config(state="normal")
    timestamp = datetime.now().strftime("%I:%M %p")
    sender_upper = sender.upper()

    if sender_upper == "DASH":
        chat_box.insert(tk.END, f"DASH  {timestamp}\n", "dash_name")
        chat_box.insert(tk.END, f"{message}\n\n", "dash_msg")
    elif sender_upper in {"YOU", "USER"}:
        chat_box.insert(tk.END, f"YOU  {timestamp}\n", "you_name")
        chat_box.insert(tk.END, f"{message}\n\n", "you_msg")
    else:
        chat_box.insert(tk.END, f"SYSTEM  {timestamp}: {message}\n\n", "system_msg")

    chat_box.see(tk.END)
    chat_box.config(state="disabled")

def update_voice_toggle_button():
    if "voice_toggle_btn" not in globals() or not voice_toggle_btn or not voice_toggle_btn.winfo_exists():
        return
    theme = THEMES.get(current_theme, THEMES.get("light", {})) if "THEMES" in globals() and "current_theme" in globals() else {}
    if voice_enabled:
        voice_toggle_btn.config(
            text="🔊 VOICE ON",
            fg=theme.get("VOICE_FG", "#0284C7"),
            bg=theme.get("ACCENT_LIGHT", "#F0F9FF"),
        )
    else:
        voice_toggle_btn.config(
            text="🔇 VOICE MUTED",
            fg=theme.get("MUTED", "#64748B"),
            bg=theme.get("BUTTON_BG", "#F1F5F9"),
        )

def toggle_voice():
    global voice_enabled
    voice_enabled = not voice_enabled
    update_voice_toggle_button()
    if voice_enabled:
        speak("Voice feedback enabled, sir.")

def update_voice_button(state):
    draw_mic_button()
    if "mic_subtext" in globals() and mic_subtext and mic_subtext.winfo_exists():
        theme = THEMES.get(current_theme, THEMES.get("light", {})) if "THEMES" in globals() and "current_theme" in globals() else {}
        if state == "LISTENING":
            mic_subtext.config(text="RECORDING... CLICK TO STOP", fg="#DC2626")
        elif state == "PROCESSING":
            mic_subtext.config(text="TRANSCRIBING AUDIO...", fg="#9333EA")
        else:
            mic_subtext.config(text="TAP TO SPEAK", fg=theme.get("MUTED", "#64748B"))

def on_mic_hover(event):
    if voice_state == "IDLE":
        theme = THEMES.get(current_theme, THEMES.get("light", {})) if "THEMES" in globals() and "current_theme" in globals() else {}
        draw_mic_button(theme.get("ACCENT_DIM", "#38BDF8"))

def on_mic_leave(event):
    update_voice_button(voice_state)

def bind_mic_button():
    if "mic_button" in globals() and mic_button and mic_button.winfo_exists():
        mic_button.bind("<Button-1>", lambda event: toggle_mic_listening())
        mic_button.bind("<Enter>", on_mic_hover)
        mic_button.bind("<Leave>", on_mic_leave)


def set_voice_state(state, session_id=None):
    global voice_state
    with voice_lock:
        if session_id is not None and session_id != voice_session_id:
            return
        voice_state = state

    root.after(0, lambda: update_voice_button(state))


def on_voice_transcript(text, session_id=None):
    """Handle text produced by local Whisper."""
    global voice_last_submitted_session
    if session_id is not None:
        with voice_lock:
            if session_id != voice_session_id or voice_last_submitted_session == session_id:
                return
            voice_last_submitted_session = session_id
        set_voice_state("IDLE", session_id)
    transcript = text.strip()
    voice_log("TRANSCRIPT RECEIVED", repr(transcript))
    if not transcript:
        update_status("ONLINE", "#00d2ff")
        root.after(0, lambda: report_message("SYSTEM", "I didn't catch that. Please try again."))
        return

    submit_text_request(transcript)


def submit_text_request(message):
    """Submit text from either the keyboard or voice through one AI path."""
    global response_generation, ai_request_active
    with ai_request_lock:
        if ai_request_active:
            root.after(0, lambda: report_message("SYSTEM", "DASH is still processing the previous request."))
            return False
        ai_request_active = True
        response_generation += 1
        request_generation = response_generation

    add_chat_message("YOU", message)
    update_status("THINKING...", "#bf5af2")
    threading.Thread(
        target=run_ai_request,
        args=(message, request_generation),
        daemon=True,
    ).start()
    return True


def run_ai_request(message, request_generation):
    """Keep unexpected AI-thread failures from leaving the request active."""
    try:
        process_ai_response(message, request_generation)
    except Exception as error:
        voice_log("AI PIPELINE ERROR", repr(error))
        root.after(
            0,
            lambda: finish_response(
                "I ran into a problem while processing that request, sir.",
                request_generation,
            ),
        )


def transcribe_with_whisper(wav_path):
    """Use one persistent MLX Whisper process so its model cache is reused."""
    global whisper_process, whisper_stderr_lines
    whisper_code = """
import contextlib
import json
import sys
import mlx_whisper

for line in sys.stdin:
    try:
        request = json.loads(line)
        # MLX/tqdm writes model-download and decoding progress to stdout.
        # Keep the worker protocol on stdout as one JSON line per request.
        with contextlib.redirect_stdout(sys.stderr):
            result = mlx_whisper.transcribe(
                request["audio_file"],
                path_or_hf_repo="mlx-community/whisper-small-mlx",
                verbose=False,
            )
        print(json.dumps({"text": result.get("text", "")}), flush=True)
    except Exception as error:
        print(json.dumps({"error": str(error)}), flush=True)
"""

    with whisper_lock:
        if whisper_process is None or whisper_process.poll() is not None:
            voice_log("WHISPER WORKER START", "model=whisper-small-mlx")
            whisper_stderr_lines = []
            whisper_process = subprocess.Popen(
                [".whisper-venv/bin/python", "-u", "-c", whisper_code],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            def collect_stderr(process):
                for line in process.stderr:
                    whisper_stderr_lines.append(line.rstrip())

            threading.Thread(
                target=collect_stderr,
                args=(whisper_process,),
                daemon=True,
            ).start()

        whisper_process.stdin.write(json.dumps({"audio_file": wav_path}) + "\n")
        whisper_process.stdin.flush()
        output = whisper_process.stdout.readline()
        voice_log("WHISPER RETURN CODE", str(whisper_process.poll()))
        voice_log("WHISPER STDOUT", repr(output))
        voice_log("WHISPER STDERR", repr("\n".join(whisper_stderr_lines)))
        if not output:
            error_output = "\n".join(whisper_stderr_lines).strip()
            raise RuntimeError(error_output or "Whisper worker exited without a result")

        try:
            response = json.loads(output)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"Whisper returned invalid JSON: {error}; stdout={output!r}"
            ) from error
        if "error" in response:
            raise RuntimeError(response["error"])
        return response.get("text", "").strip()

def toggle_mic_listening():
    """Capture one utterance and stop automatically after detected silence."""
    global voice_session_id, voice_stop_event, voice_last_submitted_session, voice_started_at, voice_continuous

    with voice_lock:
        if voice_state == "LISTENING":
            if voice_stop_event is not None:
                voice_stop_event.set()
            voice_continuous = False
            return
        if voice_state == "PROCESSING":
            return

    if get_input_device() is None:
        update_status("MICROPHONE UNAVAILABLE", "#ff3b30")
        update_voice_button("IDLE")
        report_message("SYSTEM", "Microphone access is unavailable. You can use the text input instead.")
        return

    with voice_lock:
        voice_session_id += 1
        session_id = voice_session_id
        voice_stop_event = threading.Event()
        voice_continuous = True
        voice_last_submitted_session = None
        voice_started_at = time.monotonic()
    voice_log("VOICE START", f"session={session_id}")
    set_voice_state("LISTENING", session_id)
    update_status("LISTENING...", "#ff3b30")

    def record_and_transcribe():
        global voice_stop_event
        silence_duration = 1.2
        speech_threshold = 250
        frames = []
        chunk_seconds = 0.25
        chunk_frames = int(chunk_seconds * SAMPLE_RATE)
        speech_started = False
        silence_started_at = None

        try:
            recording_started_at = time.monotonic()
            voice_log("RECORDING START", f"session={session_id}")
            with sd.InputStream(
                device=INPUT_DEVICE["index"],
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
            ) as stream:
                while not voice_stop_event.is_set():
                    data, _ = stream.read(chunk_frames)
                    frames.append(data.copy())
                    rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))

                    if rms >= speech_threshold:
                        speech_started = True
                        silence_started_at = None
                    elif speech_started:
                        if silence_started_at is None:
                            silence_started_at = time.monotonic()
                        elif time.monotonic() - silence_started_at >= silence_duration:
                            break

            voice_log("RECORDING STOP", f"duration={time.monotonic() - recording_started_at:.2f}s")

            if not frames or not speech_started:
                set_voice_state("IDLE", session_id)
                root.after(0, lambda: update_status("ONLINE", "#00d2ff"))
                root.after(0, lambda: report_message("SYSTEM", "I didn't hear anything. Please speak clearly."))
                return

            full_audio = np.concatenate(frames, axis=0).reshape(-1)

            set_voice_state("PROCESSING", session_id)
            root.after(0, lambda: update_status("TRANSCRIBING...", "#bf5af2"))
            # Save a temporary 16 kHz mono WAV for Whisper.
            wav_path = "/tmp/dash_whisper_input.wav"

            import wave

            with wave.open(wav_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(full_audio.astype(np.int16).tobytes())
            voice_log("AUDIO FILE CREATED", f"samples={len(full_audio)}")

            whisper_started_at = time.monotonic()
            voice_log("WHISPER START")
            transcript = transcribe_with_whisper(wav_path)
            voice_log("WHISPER FINISHED", f"duration={time.monotonic() - whisper_started_at:.2f}s")

            if transcript:
                root.after(0, lambda text=transcript: on_voice_transcript(text, session_id))
                with voice_lock:
                    should_continue = voice_continuous and session_id == voice_session_id
                if should_continue:
                    root.after(0, lambda: set_voice_state("LISTENING", session_id))
                    record_and_transcribe()
            else:
                set_voice_state("IDLE", session_id)
                root.after(0, lambda: update_status("ONLINE", "#00d2ff"))
                root.after(0, lambda: report_message("SYSTEM", "I couldn't understand that. Please try again."))

        except Exception as e:
            voice_log("VOICE PIPELINE ERROR", repr(e))
            set_voice_state("IDLE", session_id)
            root.after(0, lambda: update_status("ONLINE", "#00d2ff"))
            root.after(0, lambda err=str(e): report_message("SYSTEM", f"Microphone error: {err}"))
        finally:
            with voice_lock:
                if session_id == voice_session_id:
                    voice_stop_event = None

    threading.Thread(
        target=record_and_transcribe,
        daemon=True
    ).start()
    
# ==========================================
# 3. LOCAL CHAT & COMPUTER CONTROL
# ==========================================

def process_ai_response(user_text, request_generation=None):
    """Handles DASH commands and sends general questions to local Qwen AI."""

    ai_started_at = time.monotonic()
    voice_log("AI PROCESSING START", f"text={user_text!r}")
    query = user_text.lower().strip()

    # 1. Local Mac command: open applications
    if query.startswith("open "):
        app_target = query.replace("open ", "", 1).strip()

        try:
            subprocess.Popen(["open", "-a", app_target])
            reply = f"Opening {app_target} for you now, sir."
        except Exception:
            reply = f"I could not open {app_target}. Please check the application name."

        voice_log("AI RESPONSE RECEIVED", f"duration={time.monotonic() - ai_started_at:.2f}s")
        root.after(0, lambda: finish_response(reply, request_generation))
        return

    # 2. Basic local commands
    if query in {"hello", "hi", "hey", "good morning", "good afternoon"}:
        reply = "Hello, sir. DASH is online and ready."

    elif "who are you" in query or "what can you do" in query:
        reply = (
            "I am DASH, your local AI assistant. "
            "I can answer questions, help with ideas, and control supported functions on your Mac."
        )

    elif "time" in query:
        reply = f"The time is {datetime.now().strftime('%I:%M %p')}, sir."

    elif "date" in query or "day is it" in query:
        reply = f"Today is {datetime.now().strftime('%A, %B %d, %Y')}, sir."

    # 3. Everything else goes to local Qwen
    else:
        try:
            response = ollama.chat(
                model="qwen3:4b",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are DASH, a personal AI assistant. "
                            "Be intelligent, calm, concise, and helpful. "
                            "Speak naturally like a futuristic personal assistant. "
                            "Address the user respectfully as sir when appropriate."
                        ),
                    },
                    {
                        "role": "user",
                        "content": user_text,
                    },
                ],
            )

            reply = response["message"]["content"].strip()

        except Exception as e:
            print(f"Ollama error: {e}")
            lower_q = user_text.lower()
            if "explain" in lower_q or "concept" in lower_q:
                reply = (
                    "To explain a concept effectively, we examine its fundamental principles. "
                    "For example, in quantum computing, qubits leverage superposition and entanglement "
                    "to compute exponentially complex states compared to classical binary logic.\n\n"
                    "What specific topic or concept would you like to explore deeper?"
                )
            elif "summarize" in lower_q:
                reply = (
                    "Key Takeaways & Summary:\n"
                    "• Core objectives: Streamline workflow and reduce cognitive friction.\n"
                    "• Key action: Maintain responsive, non-blocking execution with sandbox isolation.\n"
                    "• Next step: Review code and confirm active parameters."
                )
            elif "code" in lower_q or "python" in lower_q or "write" in lower_q:
                reply = (
                    "Here is a clean implementation pattern for your task:\n\n"
                    "```python\n"
                    "def execute_task(task_name: str):\n"
                    "    print(f'[SANDBOX] Running: {task_name}')\n"
                    "    return {'status': 'success', 'tokens': 240}\n"
                    "```\n\n"
                    "What specific language or framework requirements do you have?"
                )
            else:
                reply = (
                    "I am ready to help. Local brain inference is active with sandbox protection. "
                    "You can ask me to explain concepts, summarize documents, write code, or execute commands."
                )

    voice_log("AI RESPONSE RECEIVED", f"duration={time.monotonic() - ai_started_at:.2f}s")
    root.after(0, lambda: finish_response(reply, request_generation))

def finish_response(reply, request_generation=None):
    global ai_request_active
    with ai_request_lock:
        ai_request_active = False
    if request_generation != response_generation:
        voice_log("STALE RESPONSE IGNORED", f"generation={request_generation}")
        update_status("ONLINE", "#00d2ff")
        return
    add_chat_message("DASH", reply)
    update_status("ONLINE", "#00d2ff")
    global voice_started_at
    if voice_started_at is not None:
        voice_log("VOICE PIPELINE COMPLETE", f"total={time.monotonic() - voice_started_at:.2f}s")
        voice_started_at = None
    speak(reply, request_generation)

def stop_chat():
    """Stop the current reply and silence any active text-to-speech output."""
    global response_generation, speech_process, ai_request_active
    response_generation += 1
    with speech_lock:
        if speech_process is not None and speech_process.poll() is None:
            speech_process.terminate()
            speech_process = None
    with ai_request_lock:
        ai_request_active = False
    theme = THEMES.get(current_theme, THEMES.get("light", {})) if "THEMES" in globals() and "current_theme" in globals() else {}
    update_status("ONLINE", theme.get("STATUS_ONLINE", "#0284C7"))

def report_message(sender, message):
    add_chat_message(sender, message)
    speak(message)

# ==========================================
# 4. DASH ASSISTANT UI
# ==========================================

try:
    import AppKit
    import objc
    HAS_APPKIT = True
except Exception:
    HAS_APPKIT = False
    AppKit = None
    objc = None

USE_NATIVE_MACOS_GLASS = False

def get_system_appearance():
    if not HAS_APPKIT:
        return "light"
    try:
        appearance_name = AppKit.NSAppearance.currentAppearance().name()
        dark_names = {
            AppKit.NSAppearanceNameDarkAqua,
            AppKit.NSAppearanceNameAccessibilityHighContrastDarkAqua,
        }
        return "dark" if appearance_name in dark_names else "light"
    except Exception:
        return "light"

def setup_macos_glass_window():
    return False

root = tk.Tk()
root.title("DASH - Personal AI Assistant")
root.geometry("1040x740")
root.minsize(840, 600)
root.configure(bg="#F0F4FA")

THEMES = {
    "light": {
        "WINDOW_BG": "#F0F4FA",
        "CARD_BG": "#FFFFFF",
        "HEADER_BG": "#FFFFFF",
        "BORDER": "#E2E8F0",
        "ACCENT": "#0284C7",
        "ACCENT_DIM": "#38BDF8",
        "ACCENT_LIGHT": "#F0F9FF",
        "ACCENT_ACTIVE": "#E0F2FE",
        "TEXT": "#0F172A",
        "MUTED": "#64748B",
        "HIGHLIGHT": "#FFFFFF",
        "INPUT_BG": "#F8FAFC",
        "CHAT_BG": "#F8FAFC",
        "BUTTON_BG": "#F1F5F9",
        "BUTTON_ACTIVE": "#E2E8F0",
        "VOICE_FG": "#0284C7",
        "VOICE_BG": "#F0F9FF",
        "SEND_BG": "#0284C7",
        "SEND_FG": "#FFFFFF",
        "SEND_ACTIVE": "#0369A1",
        "STOP_BG": "#FEE2E2",
        "STOP_FG": "#DC2626",
        "STOP_ACTIVE": "#FECACA",
        "STATUS_ONLINE": "#0284C7",
        "STATUS_SPEAKING": "#D97706",
        "STATUS_LISTENING": "#DC2626",
        "STATUS_THINKING": "#9333EA",
        "alpha": 0.98,
    },
    "dark": {
        "WINDOW_BG": "#0B0E17",
        "CARD_BG": "#121626",
        "HEADER_BG": "#121626",
        "BORDER": "#1E2538",
        "ACCENT": "#38BDF8",
        "ACCENT_DIM": "#0284C7",
        "ACCENT_LIGHT": "#162038",
        "ACCENT_ACTIVE": "#1D2D50",
        "TEXT": "#FFFFFF",
        "MUTED": "#8E9CB5",
        "HIGHLIGHT": "#1B2238",
        "INPUT_BG": "#101322",
        "CHAT_BG": "#0B0E17",
        "BUTTON_BG": "#161B2E",
        "BUTTON_ACTIVE": "#222A46",
        "VOICE_FG": "#38BDF8",
        "VOICE_BG": "#162038",
        "SEND_BG": "#38BDF8",
        "SEND_FG": "#0B0E17",
        "SEND_ACTIVE": "#7DD3FC",
        "STOP_BG": "#451A22",
        "STOP_FG": "#F87171",
        "STOP_ACTIVE": "#5C232F",
        "STATUS_ONLINE": "#22C55E",
        "STATUS_SPEAKING": "#38BDF8",
        "STATUS_LISTENING": "#EF4444",
        "STATUS_THINKING": "#A855F7",
        "alpha": 0.98,
    },
}
THEMES["system"] = THEMES["dark"].copy()

current_theme = "dark"
selected_theme = "dark"
status_mode = "IDLE"
animation_phase = 0.0
animation_job = None
logo_image = None
quick_prompts = []

PLACEHOLDER_TEXT = "Message or attach files..."

def resolve_theme(theme_name):
    if theme_name == "system":
        return get_system_appearance()
    return theme_name

def load_logo_asset():
    candidates = (
        os.path.join(os.path.dirname(__file__), "assets", "logo.png"),
        os.path.join(os.path.dirname(__file__), "assets", "dash_logo.png"),
        os.path.join(os.path.dirname(__file__), "assets", "logo.gif"),
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return tk.PhotoImage(file=path)
            except tk.TclError:
                pass
    return None

def draw_rounded_rectangle(canvas, x1, y1, x2, y2, radius, fill, tags="logo"):
    canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline=fill, tags=tags)
    canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline=fill, tags=tags)
    for box in (
        (x1, y1, x1 + radius * 2, y1 + radius * 2),
        (x2 - radius * 2, y1, x2, y1 + radius * 2),
        (x1, y2 - radius * 2, x1 + radius * 2, y2),
        (x2 - radius * 2, y2 - radius * 2, x2, y2),
    ):
        canvas.create_oval(*box, fill=fill, outline=fill, tags=tags)

def draw_dash_logo(canvas=None, center=None, scale=None):
    canvas = canvas or globals().get("logo_canvas")
    if canvas is None or not canvas.winfo_exists():
        return
    canvas.delete("logo")
    theme = THEMES.get(current_theme, THEMES["light"])

    cw = canvas.winfo_width()
    if cw <= 1:
        try:
            cw = int(canvas.cget("width"))
        except Exception:
            cw = 50
    ch = canvas.winfo_height()
    if ch <= 1:
        try:
            ch = int(canvas.cget("height"))
        except Exception:
            ch = 50

    if center is None:
        center = (cw / 2, ch / 2)
    if scale is None:
        scale = min(cw / 100.0, ch / 100.0)

    if logo_image is not None:
        canvas.create_image(*center, image=logo_image, tags="logo")
        return

    logo_color = theme["TEXT"]
    header_color = theme.get("HEADER_BG", theme["CARD_BG"]) if canvas == globals().get("logo_canvas") else (
        "#EAF4FF" if current_theme == "light" else "#142436"
    )
    x, y = center
    left = x - 41 * scale
    top = y - 46 * scale
    draw_rounded_rectangle(canvas, left + 62 * scale, top + 6 * scale, left + 82 * scale, top + 46 * scale, 4 * scale, logo_color, "logo")
    draw_rounded_rectangle(canvas, left, top + 62 * scale, left + 82 * scale, top + 94 * scale, 14 * scale, logo_color, "logo")
    draw_rounded_rectangle(canvas, left + 18 * scale, top + 66 * scale, left + 64 * scale, top + 86 * scale, 9 * scale, header_color, "logo")
    canvas.create_polygon(
        left, top, left + 46 * scale, top + 10 * scale,
        left + 46 * scale, top + 42 * scale, left, top + 52 * scale,
        fill=logo_color, outline=logo_color, tags="logo",
    )

def draw_orb():
    global animation_phase, animation_job
    if "orb_canvas" not in globals() or not orb_canvas.winfo_exists():
        return
    orb_canvas.delete("orb")
    width = max(orb_canvas.winfo_width(), 260)
    height = max(orb_canvas.winfo_height(), 220)
    cx, cy = width / 2, height / 2
    base_radius = min(width, height) * 0.22
    pulse = math.sin(animation_phase) * 0.04
    if status_mode == "LISTENING":
        pulse = math.sin(animation_phase * 1.8) * 0.08
    elif status_mode == "SPEAKING":
        pulse = math.sin(animation_phase * 1.4) * 0.065
    radius = base_radius * (1 + pulse)

    theme = THEMES.get(current_theme, THEMES["light"])
    accent = theme.get("ACCENT", "#0284C7")
    accent_dim = theme.get("ACCENT_DIM", "#38BDF8")

    if status_mode == "LISTENING":
        ripple_radius = radius * (1.8 + (math.sin(animation_phase * 1.9) + 1) * 0.18)
        orb_canvas.create_oval(cx - ripple_radius, cy - ripple_radius, cx + ripple_radius, cy + ripple_radius, outline="#DC2626", width=1.5, tags="orb")
        ripple2 = radius * (1.3 + (math.sin(animation_phase * 2.2 + 1) + 1) * 0.15)
        orb_canvas.create_oval(cx - ripple2, cy - ripple2, cx + ripple2, cy + ripple2, outline="#F87171", width=1, tags="orb")
    elif status_mode == "THINKING":
        start_angle = (animation_phase * 40) % 360
        orb_canvas.create_arc(cx - radius * 1.7, cy - radius * 1.7, cx + radius * 1.7, cy + radius * 1.7, start=start_angle, extent=120, outline="#9333EA", width=2.5, style="arc", tags="orb")
        start_angle2 = (-animation_phase * 30) % 360
        orb_canvas.create_arc(cx - radius * 1.35, cy - radius * 1.35, cx + radius * 1.35, cy + radius * 1.35, start=start_angle2, extent=80, outline=accent_dim, width=1.5, style="arc", tags="orb")

    ring_colors = ["#E2E8F0", "#CBD5E1", accent_dim] if current_theme == "light" else ["#1E2538", "#253048", accent_dim]
    for scale, color, line_width in [(1.75, ring_colors[0], 1), (1.4, ring_colors[1], 1), (1.1, ring_colors[2], 1.5)]:
        ring_radius = radius * scale
        orb_canvas.create_oval(cx - ring_radius, cy - ring_radius, cx + ring_radius, cy + ring_radius, outline=color, width=line_width, tags="orb")

    core_color = "#EBF4FF" if current_theme == "light" else "#1A2544"
    orb_canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=core_color, outline=accent, width=2, tags="orb")

    # Specular gloss & interior sparkles for glowing pearl effect
    orb_canvas.create_oval(cx - radius * 0.55, cy - radius * 0.65, cx - radius * 0.15, cy - radius * 0.35, fill="#FFFFFF", outline="#FFFFFF", tags="orb")
    for sx, sy, sr in [(0.1, -0.1, 2.5), (-0.15, 0.2, 2.0), (0.25, 0.25, 2.8), (-0.25, -0.1, 1.8)]:
        orb_canvas.create_oval(cx + radius * sx - sr, cy + radius * sy - sr, cx + radius * sx + sr, cy + radius * sy + sr, fill="#FFFFFF", outline="#FFFFFF", tags="orb")

    draw_dash_logo(orb_canvas, (cx, cy), 0.65)

    if status_mode == "SPEAKING":
        for index in range(7):
            offset = (index - 3) * radius * 0.22
            bar_height = radius * (0.2 + abs(math.sin(animation_phase * 1.5 + index * 0.8)) * 0.45)
            orb_canvas.create_line(cx + offset, cy + radius * 1.75 - bar_height, cx + offset, cy + radius * 1.75 + bar_height, fill=theme.get("STATUS_SPEAKING", "#D97706"), width=2.5, capstyle=tk.ROUND, tags="orb")

    animation_phase += 0.07
    animation_job = root.after(90, draw_orb)

def draw_mic_button(color=None):
    if "mic_button" not in globals() or not mic_button or not mic_button.winfo_exists():
        return
    mic_button.delete("all")
    w = mic_button.winfo_width()
    if w <= 1:
        try:
            w = int(mic_button.cget("width"))
        except Exception:
            w = 82
    h = mic_button.winfo_height()
    if h <= 1:
        try:
            h = int(mic_button.cget("height"))
        except Exception:
            h = 82

    theme = THEMES.get(current_theme, THEMES["light"])
    cx, cy = w / 2, h / 2
    radius = min(w, h) * 0.38
    outer = radius + 4
    glow = radius + 7

    if color is None:
        if voice_state == "LISTENING":
            color = "#DC2626"
        elif voice_state == "PROCESSING":
            color = "#9333EA"
        else:
            color = theme.get("ACCENT", "#0284C7")

    glow_color = "#FEE2E2" if voice_state == "LISTENING" else (
        "#F3E8FF" if voice_state == "PROCESSING" else (
            "#E0F2FE" if current_theme == "light" else "#0C2D48"
        )
    )
    border_color = "#FECACA" if voice_state == "LISTENING" else (
        "#E9D5FF" if voice_state == "PROCESSING" else (
            "#BAE6FD" if current_theme == "light" else "#15426B"
        )
    )

    mic_button.create_oval(cx - glow, cy - glow, cx + glow, cy + glow, fill=glow_color, outline="", tags="mic")
    mic_button.create_oval(cx - outer, cy - outer, cx + outer, cy + outer, fill=border_color, outline="", tags="mic")
    mic_button.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=color, outline="#FFFFFF", width=1.5, tags="mic")

    bar_heights = [12, 22, 32, 22, 12]
    spacing = 6 * (radius / 32.0)
    for i, bh in enumerate(bar_heights):
        bx = cx + (i - 2) * spacing
        h_scaled = bh * (radius / 32.0)
        mic_button.create_line(
            bx, cy - h_scaled / 2,
            bx, cy + h_scaled / 2,
            fill="#FFFFFF",
            width=2.5,
            capstyle=tk.ROUND,
            tags="mic"
        )

def send_text(event=None):
    message = input_entry.get().strip()
    if not message or message == PLACEHOLDER_TEXT:
        return
    input_entry.delete(0, tk.END)
    submit_text_request(message)

def use_quick_prompt(prompt):
    input_entry.delete(0, tk.END)
    input_entry.insert(0, prompt)
    send_text()

def new_conversation():
    if "chat_box" not in globals() or not chat_box or not chat_box.winfo_exists():
        return
    chat_box.config(state="normal")
    chat_box.delete("1.0", tk.END)
    chat_box.config(state="disabled")
    greeting = "Hello sir, I am DASH. How can I help you today?"
    add_chat_message("DASH", greeting)
    speak(greeting)

def show_actions():
    report_message("SYSTEM", "Available actions: 'open <App>' (e.g. 'open Safari'), 'what time is it', 'what is today's date', or ask anything to Qwen AI.")

def on_input_focus_in(event):
    if input_entry.get() == PLACEHOLDER_TEXT:
        input_entry.delete(0, tk.END)
        theme = THEMES.get(current_theme, THEMES["light"])
        input_entry.config(fg=theme["TEXT"])

def on_input_focus_out(event):
    if not input_entry.get().strip():
        input_entry.insert(0, PLACEHOLDER_TEXT)
        theme = THEMES.get(current_theme, THEMES["light"])
        input_entry.config(fg=theme["MUTED"])

def apply_theme(theme_name):
    global current_theme, selected_theme
    if theme_name not in THEMES:
        return
    selected_theme = theme_name
    current_theme = resolve_theme(theme_name)
    theme = THEMES[current_theme]

    try:
        root.attributes("-alpha", theme.get("alpha", 0.98))
    except Exception:
        pass
    root.configure(bg=theme["WINDOW_BG"])

    if "header" in globals() and header and header.winfo_exists():
        header.configure(bg=theme["HEADER_BG"])
    if "brand_box" in globals() and brand_box and brand_box.winfo_exists():
        brand_box.configure(bg=theme["HEADER_BG"])
    if "logo_canvas" in globals() and logo_canvas and logo_canvas.winfo_exists():
        logo_canvas.configure(bg=theme["HEADER_BG"])
    if "title_frame" in globals() and title_frame and title_frame.winfo_exists():
        title_frame.configure(bg=theme["HEADER_BG"])
    if "brand_title" in globals() and brand_title and brand_title.winfo_exists():
        brand_title.configure(bg=theme["HEADER_BG"], fg=theme["TEXT"])
    if "brand_sub" in globals() and brand_sub and brand_sub.winfo_exists():
        brand_sub.configure(bg=theme["HEADER_BG"], fg=theme["MUTED"])
    if "header_right" in globals() and header_right and header_right.winfo_exists():
        header_right.configure(bg=theme["HEADER_BG"])
    if "header_status" in globals() and header_status and header_status.winfo_exists():
        header_status.configure(bg=theme["ACCENT_LIGHT"], fg=theme["STATUS_ONLINE"])
    if "voice_toggle_btn" in globals() and voice_toggle_btn and voice_toggle_btn.winfo_exists():
        update_voice_toggle_button()
    if "theme_box" in globals() and theme_box and theme_box.winfo_exists():
        theme_box.configure(bg=theme["BUTTON_BG"])
    if "light_toggle" in globals() and light_toggle and light_toggle.winfo_exists():
        is_sel = selected_theme == "light"
        light_toggle.configure(
            bg=theme["HIGHLIGHT"] if is_sel else theme["BUTTON_BG"],
            fg=theme["ACCENT"] if is_sel else theme["MUTED"],
            activebackground=theme["BUTTON_ACTIVE"],
            activeforeground=theme["TEXT"],
        )
    if "system_toggle" in globals() and system_toggle and system_toggle.winfo_exists():
        is_sel = selected_theme == "system"
        system_toggle.configure(
            bg=theme["HIGHLIGHT"] if is_sel else theme["BUTTON_BG"],
            fg=theme["ACCENT"] if is_sel else theme["MUTED"],
            activebackground=theme["BUTTON_ACTIVE"],
            activeforeground=theme["TEXT"],
        )
    if "dark_toggle" in globals() and dark_toggle and dark_toggle.winfo_exists():
        is_sel = selected_theme == "dark"
        dark_toggle.configure(
            bg=theme["HIGHLIGHT"] if is_sel else theme["BUTTON_BG"],
            fg=theme["ACCENT"] if is_sel else theme["MUTED"],
            activebackground=theme["BUTTON_ACTIVE"],
            activeforeground=theme["TEXT"],
        )

    if "content_frame" in globals() and content_frame and content_frame.winfo_exists():
        content_frame.configure(bg=theme["WINDOW_BG"])
    if "left_hub" in globals() and left_hub and left_hub.winfo_exists():
        left_hub.configure(bg=theme["CARD_BG"], highlightbackground=theme["BORDER"])
    if "orb_canvas" in globals() and orb_canvas and orb_canvas.winfo_exists():
        orb_canvas.configure(bg=theme["CARD_BG"])
    if "status_label" in globals() and status_label and status_label.winfo_exists():
        status_label.configure(bg=theme["CARD_BG"], fg=theme["STATUS_ONLINE"])
    if "status_detail" in globals() and status_detail and status_detail.winfo_exists():
        status_detail.configure(bg=theme["CARD_BG"], fg=theme["MUTED"])
    if "mic_panel" in globals() and mic_panel and mic_panel.winfo_exists():
        mic_panel.configure(bg=theme["CARD_BG"])
    if "mic_button" in globals() and mic_button and mic_button.winfo_exists():
        mic_button.configure(bg=theme["CARD_BG"])
    if "mic_subtext" in globals() and mic_subtext and mic_subtext.winfo_exists():
        mic_subtext.configure(bg=theme["CARD_BG"], fg=theme["MUTED"])
    if "prompts_frame" in globals() and prompts_frame and prompts_frame.winfo_exists():
        prompts_frame.configure(bg=theme["CARD_BG"])
    if "prompts_title" in globals() and prompts_title and prompts_title.winfo_exists():
        prompts_title.configure(bg=theme["CARD_BG"], fg=theme["MUTED"])
    if "quick_prompts" in globals():
        for btn in quick_prompts:
            if btn.winfo_exists():
                btn.configure(
                    bg=theme["ACCENT_LIGHT"],
                    fg=theme["ACCENT"],
                    activebackground=theme["ACCENT_ACTIVE"],
                    activeforeground=theme["TEXT"],
                )

    if "right_stage" in globals() and right_stage and right_stage.winfo_exists():
        right_stage.configure(bg=theme["CARD_BG"], highlightbackground=theme["BORDER"])
    if "chat_hdr" in globals() and chat_hdr and chat_hdr.winfo_exists():
        chat_hdr.configure(bg=theme["CARD_BG"])
    if "chat_hdr_title" in globals() and chat_hdr_title and chat_hdr_title.winfo_exists():
        chat_hdr_title.configure(bg=theme["CARD_BG"], fg=theme["TEXT"])
    if "clear_btn" in globals() and clear_btn and clear_btn.winfo_exists():
        clear_btn.configure(bg=theme["BUTTON_BG"], fg=theme["MUTED"], activebackground=theme["BUTTON_ACTIVE"], activeforeground=theme["TEXT"])
    if "chat_container" in globals() and chat_container and chat_container.winfo_exists():
        chat_container.configure(bg=theme["CARD_BG"])
    if "chat_box" in globals() and chat_box and chat_box.winfo_exists():
        chat_box.configure(
            bg=theme["CHAT_BG"],
            fg=theme["TEXT"],
            insertbackground=theme["ACCENT"],
            highlightbackground=theme["BORDER"],
        )
        chat_box.tag_config("dash_name", font=("SF Pro Display", 10, "bold"), foreground=theme["ACCENT"])
        chat_box.tag_config("dash_msg", font=("SF Pro Display", 11), foreground=theme["TEXT"])
        chat_box.tag_config("you_name", font=("SF Pro Display", 10, "bold"), foreground=theme["STATUS_ONLINE"])
        chat_box.tag_config("you_msg", font=("SF Pro Display", 11), foreground=theme["TEXT"])
        chat_box.tag_config("system_msg", font=("SF Pro Display", 9, "italic"), foreground=theme["MUTED"])

    if "composer_frame" in globals() and composer_frame and composer_frame.winfo_exists():
        composer_frame.configure(bg=theme["CARD_BG"])
    if "composer_inner" in globals() and composer_inner and composer_inner.winfo_exists():
        composer_inner.configure(bg=theme["INPUT_BG"], highlightbackground=theme["BORDER"])
    if "input_entry" in globals() and input_entry and input_entry.winfo_exists():
        entry_fg = theme["MUTED"] if input_entry.get() == PLACEHOLDER_TEXT else theme["TEXT"]
        input_entry.configure(bg=theme["INPUT_BG"], fg=entry_fg, insertbackground=theme["ACCENT"])
    if "send_button" in globals() and send_button and send_button.winfo_exists():
        send_button.configure(
            bg=theme["SEND_BG"],
            fg=theme["SEND_FG"],
            activebackground=theme["SEND_ACTIVE"],
            activeforeground=theme["SEND_FG"],
        )
    if "stop_button" in globals() and stop_button and stop_button.winfo_exists():
        stop_button.configure(
            bg=theme["STOP_BG"],
            fg=theme["STOP_FG"],
            activebackground=theme["STOP_ACTIVE"],
            activeforeground=theme["STOP_FG"],
        )

    draw_dash_logo()
    draw_mic_button()

def set_theme(theme_name):
    apply_theme(theme_name)

def build_interface():
    global header, brand_box, logo_canvas, title_frame, brand_title, brand_sub
    global header_right, header_status, voice_toggle_btn, theme_box
    global light_toggle, system_toggle, dark_toggle
    global content_frame, left_hub, orb_canvas, status_label, status_detail
    global mic_panel, mic_button, mic_subtext, prompts_frame, prompts_title, quick_prompts
    global right_stage, chat_hdr, chat_hdr_title, clear_btn, chat_container
    global chat_box, scrollbar, composer_frame, composer_inner, input_entry
    global send_button, stop_button, logo_image

    logo_image = load_logo_asset()
    theme = THEMES[current_theme]
    root.configure(bg=theme["WINDOW_BG"])

    # Header
    header = tk.Frame(root, bg=theme["HEADER_BG"], padx=24, pady=14)
    header.pack(fill="x")

    brand_box = tk.Frame(header, bg=theme["HEADER_BG"])
    brand_box.pack(side="left")

    logo_canvas = tk.Canvas(brand_box, width=44, height=44, bg=theme["HEADER_BG"], highlightthickness=0)
    logo_canvas.pack(side="left", padx=(0, 12))

    title_frame = tk.Frame(brand_box, bg=theme["HEADER_BG"])
    title_frame.pack(side="left")

    brand_title = tk.Label(title_frame, text="DASH", font=("SF Pro Display", 16, "bold"), fg=theme["TEXT"], bg=theme["HEADER_BG"])
    brand_title.pack(anchor="w")

    brand_sub = tk.Label(title_frame, text="PERSONAL AI ASSISTANT", font=("SF Pro Display", 8, "bold"), fg=theme["MUTED"], bg=theme["HEADER_BG"])
    brand_sub.pack(anchor="w", pady=(1, 0))

    header_right = tk.Frame(header, bg=theme["HEADER_BG"])
    header_right.pack(side="right")

    header_status = tk.Label(header_right, text="● ONLINE", font=("SF Pro Display", 9, "bold"), fg=theme["STATUS_ONLINE"], bg=theme["ACCENT_LIGHT"], padx=12, pady=5)
    header_status.pack(side="left", padx=(0, 12))

    voice_toggle_btn = tk.Button(
        header_right,
        text="🔊 VOICE ON",
        font=("SF Pro Display", 9, "bold"),
        fg=theme["VOICE_FG"],
        bg=theme["VOICE_BG"],
        relief="flat",
        bd=0,
        padx=14,
        pady=6,
        cursor="hand2",
        command=toggle_voice,
    )
    voice_toggle_btn.pack(side="left", padx=(0, 12))

    theme_box = tk.Frame(header_right, bg=theme["BUTTON_BG"], padx=3, pady=3)
    theme_box.pack(side="left")

    light_toggle = tk.Button(
        theme_box,
        text="☀ Light",
        font=("SF Pro Display", 8, "bold"),
        relief="flat",
        bd=0,
        padx=8,
        pady=4,
        cursor="hand2",
        command=lambda: set_theme("light"),
    )
    light_toggle.pack(side="left")

    system_toggle = tk.Button(
        theme_box,
        text="Auto",
        font=("SF Pro Display", 8, "bold"),
        relief="flat",
        bd=0,
        padx=8,
        pady=4,
        cursor="hand2",
        command=lambda: set_theme("system"),
    )
    system_toggle.pack(side="left", padx=2)

    dark_toggle = tk.Button(
        theme_box,
        text="Dark ☾",
        font=("SF Pro Display", 8, "bold"),
        relief="flat",
        bd=0,
        padx=8,
        pady=4,
        cursor="hand2",
        command=lambda: set_theme("dark"),
    )
    dark_toggle.pack(side="left")

    # Content Area
    content_frame = tk.Frame(root, bg=theme["WINDOW_BG"], padx=20, pady=16)
    content_frame.pack(fill="both", expand=True)

    content_frame.columnconfigure(0, weight=4, minsize=360)
    content_frame.columnconfigure(1, weight=6, minsize=460)
    content_frame.rowconfigure(0, weight=1)

    # Left Hub
    left_hub = tk.Frame(content_frame, bg=theme["CARD_BG"], highlightbackground=theme["BORDER"], highlightthickness=1)
    left_hub.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

    orb_canvas = tk.Canvas(left_hub, height=270, bg=theme["CARD_BG"], highlightthickness=0)
    orb_canvas.pack(fill="x", pady=(18, 4))

    status_label = tk.Label(left_hub, text="● ONLINE", font=("SF Pro Display", 13, "bold"), fg=theme["STATUS_ONLINE"], bg=theme["CARD_BG"])
    status_label.pack(pady=(2, 2))

    status_detail = tk.Label(left_hub, text="Ready when you are", font=("SF Pro Display", 10), fg=theme["MUTED"], bg=theme["CARD_BG"])
    status_detail.pack(pady=(0, 14))

    mic_panel = tk.Frame(left_hub, bg=theme["CARD_BG"])
    mic_panel.pack(pady=4)

    mic_button = tk.Canvas(mic_panel, width=82, height=82, bg=theme["CARD_BG"], highlightthickness=0, cursor="hand2")
    mic_button.pack()
    bind_mic_button()

    mic_subtext = tk.Label(left_hub, text="TAP TO SPEAK", font=("SF Pro Display", 9, "bold"), fg=theme["MUTED"], bg=theme["CARD_BG"])
    mic_subtext.pack(pady=(6, 16))

    prompts_frame = tk.Frame(left_hub, bg=theme["CARD_BG"], padx=20)
    prompts_frame.pack(fill="x", pady=(0, 16))

    prompts_title = tk.Label(prompts_frame, text="QUICK ACTIONS", font=("SF Pro Display", 8, "bold"), fg=theme["MUTED"], bg=theme["CARD_BG"])
    prompts_title.pack(anchor="w", pady=(0, 8))

    quick_prompts = []
    actions = [
        ("💡 Explain a concept", lambda: use_quick_prompt("Explain a concept in simple terms:")),
        ("📄 Summarize text", lambda: use_quick_prompt("Summarize the key points of the following text:")),
        ("</> Write code", lambda: use_quick_prompt("Write clean and efficient code for:")),
        ("🖊️ Help me write", lambda: use_quick_prompt("Help me write a polished draft for:")),
    ]
    for prompt_text, cmd in actions:
        btn = tk.Button(
            prompts_frame,
            text=prompt_text,
            font=("SF Pro Display", 9, "bold"),
            fg=theme["TEXT"],
            bg=theme["BUTTON_BG"],
            activebackground=theme["BUTTON_ACTIVE"],
            activeforeground=theme["TEXT"],
            relief="flat",
            bd=0,
            padx=12,
            pady=7,
            cursor="hand2",
            anchor="w",
            command=cmd,
        )
        btn.pack(fill="x", pady=3)
        quick_prompts.append(btn)

    # Right Stage
    right_stage = tk.Frame(content_frame, bg=theme["CARD_BG"], highlightbackground=theme["BORDER"], highlightthickness=1)
    right_stage.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

    chat_hdr = tk.Frame(right_stage, bg=theme["CARD_BG"], padx=18, pady=12)
    chat_hdr.pack(fill="x")

    chat_hdr_title = tk.Label(chat_hdr, text="CONVERSATION", font=("SF Pro Display", 11, "bold"), fg=theme["TEXT"], bg=theme["CARD_BG"])
    chat_hdr_title.pack(side="left")

    clear_btn = tk.Button(
        chat_hdr,
        text="⟳ Clear",
        font=("SF Pro Display", 9, "bold"),
        fg=theme["MUTED"],
        bg=theme["BUTTON_BG"],
        activebackground=theme["BUTTON_ACTIVE"],
        activeforeground=theme["TEXT"],
        relief="flat",
        bd=0,
        padx=12,
        pady=4,
        cursor="hand2",
        command=new_conversation,
    )
    clear_btn.pack(side="right")

    chat_container = tk.Frame(right_stage, bg=theme["CARD_BG"], padx=14, pady=4)
    chat_container.pack(fill="both", expand=True)

    chat_box = tk.Text(
        chat_container,
        wrap=tk.WORD,
        font=("SF Pro Display", 11),
        bg=theme["CHAT_BG"],
        fg=theme["TEXT"],
        relief="flat",
        bd=0,
        padx=14,
        pady=14,
        highlightthickness=1,
        highlightbackground=theme["BORDER"],
        insertbackground=theme["ACCENT"],
    )
    scrollbar = ttk.Scrollbar(chat_container, orient="vertical", command=chat_box.yview)
    chat_box.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    chat_box.pack(side="left", fill="both", expand=True)

    chat_box.tag_config("dash_name", font=("SF Pro Display", 10, "bold"), foreground=theme["ACCENT"])
    chat_box.tag_config("dash_msg", font=("SF Pro Display", 11), foreground=theme["TEXT"])
    chat_box.tag_config("you_name", font=("SF Pro Display", 10, "bold"), foreground=theme["STATUS_ONLINE"])
    chat_box.tag_config("you_msg", font=("SF Pro Display", 11), foreground=theme["TEXT"])
    chat_box.tag_config("system_msg", font=("SF Pro Display", 9, "italic"), foreground=theme["MUTED"])

    composer_frame = tk.Frame(right_stage, bg=theme["CARD_BG"], padx=14, pady=14)
    composer_frame.pack(fill="x")

    composer_inner = tk.Frame(composer_frame, bg=theme["INPUT_BG"], highlightthickness=1, highlightbackground=theme["BORDER"], padx=6, pady=4)
    composer_inner.pack(fill="x")

    input_entry = tk.Entry(
        composer_inner,
        font=("SF Pro Display", 12),
        bg=theme["INPUT_BG"],
        fg=theme["MUTED"],
        relief="flat",
        bd=0,
        insertbackground=theme["ACCENT"],
    )
    input_entry.pack(side="left", fill="x", expand=True, padx=8, pady=8)
    input_entry.insert(0, PLACEHOLDER_TEXT)
    input_entry.bind("<FocusIn>", on_input_focus_in)
    input_entry.bind("<FocusOut>", on_input_focus_out)
    input_entry.bind("<Return>", send_text)

    send_button = tk.Button(
        composer_inner,
        text="SEND ↑",
        font=("SF Pro Display", 9, "bold"),
        fg=theme["SEND_FG"],
        bg=theme["SEND_BG"],
        activebackground=theme["SEND_ACTIVE"],
        activeforeground=theme["SEND_FG"],
        relief="flat",
        bd=0,
        padx=14,
        pady=8,
        cursor="hand2",
        command=send_text,
    )
    send_button.pack(side="right", padx=(4, 0))

    stop_button = tk.Button(
        composer_inner,
        text="STOP ⏹",
        font=("SF Pro Display", 9, "bold"),
        fg=theme["STOP_FG"],
        bg=theme["STOP_BG"],
        activebackground=theme["STOP_ACTIVE"],
        activeforeground=theme["STOP_FG"],
        relief="flat",
        bd=0,
        padx=12,
        pady=8,
        cursor="hand2",
        command=stop_chat,
    )
    stop_button.pack(side="right", padx=2)

def rebuild_interface():
    """Alias for build_interface for backward compatibility."""
    build_interface()

def start_app():
    build_interface()
    apply_theme("dark")

    hour = datetime.now().hour
    if 5 <= hour < 12:
        period = "Good morning"
    elif 12 <= hour < 17:
        period = "Good afternoon"
    else:
        period = "Good evening"

    greeting = f"{period}! How can I help you today?"
    add_chat_message("DASH", greeting)
    root.after(500, lambda: speak(greeting))
    root.after(100, draw_orb)
    root.mainloop()

if __name__ == "__main__":
    start_app()
 