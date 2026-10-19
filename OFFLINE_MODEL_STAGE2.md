# Offline GGUF inference — Stage 2

This patch connects the existing Model Settings screen to a native `llama.cpp` inference engine and routes the universal chat to the selected local GGUF file. If no valid local model is selected, the old server-backed chat remains available.

## Build requirements

The GitHub Actions workflow clones the current `ggml-org/llama.cpp` source into `llama.cpp`, installs Android NDK 27.2.12479018 and CMake 3.22.1, and builds the APK for `arm64-v8a`.

## Model recommendation

Use `Qwen2.5-0.5B-Instruct-Q4_K_M.gguf` for this stage. The native prompt uses the Qwen2.5 ChatML template, so arbitrary GGUF files may not respond correctly if they use a different chat template. The model file is imported into app-private storage by the existing settings screen. Allow additional free space for the imported copy and runtime memory.

## Important validation note

This patch's native Android build must be verified by the repository's GitHub Actions run. The current environment does not have Android SDK/NDK or Gradle available, so an APK build has not been locally confirmed. After Actions succeeds, install the newly built APK, select the model folder, then turn off Wi-Fi/mobile data and test a short prompt in Universal Chat.
