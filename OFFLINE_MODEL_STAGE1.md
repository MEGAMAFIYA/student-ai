# Offline AI model integration — Stage 1

This patch adds the Android settings screen and Storage Access Framework folder picker.

## Model
Recommended test model: `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` from:
https://huggingface.co/bartowski/Qwen2.5-0.5B-Instruct-GGUF

## What this patch does
- Adds a Settings / Offline AI button to the home screen.
- Opens Android's system folder picker; no broad storage permission is requested.
- Recursively searches the chosen folder for `.gguf` files.
- Copies the first found GGUF into app-private storage and remembers its path.
- Allows removing the copied file from app storage.

## Important limitation
This is **not yet a complete offline AI implementation**. The app currently has no bundled llama.cpp JNI inference engine, and `ChatActivity` still calls the remote API. The settings screen explicitly tells the user this. To finish offline chat, integrate/build the native llama.cpp Android engine (including supported ABI libraries), load the stored GGUF using that engine, and route chat prompts to local inference when a model is selected. Do not advertise offline chat as working until that engine is integrated and tested on a real device.

## Build
The repository has no Gradle wrapper. Its existing GitHub Actions workflow installs Gradle 8.7 and can build the project. Build and test through that workflow before installing the resulting APK.
