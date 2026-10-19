#include <jni.h>
#include <android/log.h>
#include <algorithm>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>
#include <vector>
#include "llama.h"

#define LOG_TAG "StudentAI-LocalLLM"
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

namespace {
struct Engine {
    llama_model *model = nullptr;
    llama_context *ctx = nullptr;
    llama_context_params context_params = llama_context_default_params();
    ~Engine() {
        if (ctx) llama_free(ctx);
        if (model) llama_model_free(model);
    }
};

void throwJava(JNIEnv *env, const char *message) {
    jclass cls = env->FindClass("java/lang/IllegalStateException");
    if (cls) env->ThrowNew(cls, message);
}

std::string fromJava(JNIEnv *env, jstring value) {
    if (!value) return {};
    const char *chars = env->GetStringUTFChars(value, nullptr);
    if (!chars) return {};
    std::string result(chars);
    env->ReleaseStringUTFChars(value, chars);
    return result;
}

bool tokenize(const llama_vocab *vocab, const std::string &prompt, std::vector<llama_token> &tokens) {
    tokens.resize(prompt.size() + 16);
    int32_t count = llama_tokenize(vocab, prompt.c_str(), static_cast<int32_t>(prompt.size()),
                                   tokens.data(), static_cast<int32_t>(tokens.size()), true, true);
    if (count < 0) {
        tokens.resize(static_cast<size_t>(-count));
        count = llama_tokenize(vocab, prompt.c_str(), static_cast<int32_t>(prompt.size()),
                               tokens.data(), static_cast<int32_t>(tokens.size()), true, true);
    }
    if (count <= 0) return false;
    tokens.resize(static_cast<size_t>(count));
    return true;
}

void fillBatch(llama_batch &batch, const std::vector<llama_token> &tokens, size_t offset, size_t count) {
    batch.n_tokens = static_cast<int32_t>(count);
    for (int32_t i = 0; i < batch.n_tokens; ++i) {
        const size_t absoluteIndex = offset + static_cast<size_t>(i);
        batch.token[i] = tokens[absoluteIndex];
        batch.pos[i] = static_cast<llama_pos>(absoluteIndex);
        batch.n_seq_id[i] = 1;
        batch.seq_id[i][0] = 0;
        batch.logits[i] = (absoluteIndex == tokens.size() - 1);
    }
}

llama_token greedyToken(Engine *engine) {
    const float *logits = llama_get_logits_ith(engine->ctx, -1);
    const llama_vocab *vocab = llama_model_get_vocab(engine->model);
    const int32_t count = llama_vocab_n_tokens(vocab);
    if (!logits || count <= 0) return -1;
    int32_t best = 0;
    float bestValue = logits[0];
    for (int32_t i = 1; i < count; ++i) {
        if (logits[i] > bestValue) {
            bestValue = logits[i];
            best = i;
        }
    }
    return static_cast<llama_token>(best);
}
}

extern "C" JNIEXPORT jlong JNICALL
Java_uz_studentai_mobile_local_LocalLlamaEngine_nativeLoadModel(JNIEnv *env, jobject, jstring pathValue) {
    const std::string path = fromJava(env, pathValue);
    if (path.empty()) {
        throwJava(env, "Model fayli yo'li bo'sh.");
        return 0;
    }
    static bool backendInitialized = false;
    if (!backendInitialized) {
        llama_backend_init();
        backendInitialized = true;
    }

    auto engine = std::make_unique<Engine>();
    llama_model_params modelParams = llama_model_default_params();
    modelParams.n_gpu_layers = 0; // Telefonda protsessor orqali ishlaydi.
    engine->model = llama_model_load_from_file(path.c_str(), modelParams);
    if (!engine->model) {
        throwJava(env, "GGUF modelini yuklab bo'lmadi. Fayl buzilgan yoki format mos emas.");
        return 0;
    }
    engine->context_params.n_ctx = 2048;
    engine->context_params.n_batch = 512;
    const unsigned int cores = std::max(2u, std::thread::hardware_concurrency());
    engine->context_params.n_threads = static_cast<int32_t>(std::min(4u, cores));
    engine->context_params.n_threads_batch = engine->context_params.n_threads;
    engine->ctx = llama_init_from_model(engine->model, engine->context_params);
    if (!engine->ctx) {
        throwJava(env, "Model uchun xotira ajratilmadi. Boshqa ilovalarni yoping yoki kichikroq model tanlang.");
        return 0;
    }
    return reinterpret_cast<jlong>(engine.release());
}

extern "C" JNIEXPORT jstring JNICALL
Java_uz_studentai_mobile_local_LocalLlamaEngine_nativeGenerate(JNIEnv *env, jobject, jlong handle, jstring promptValue, jint maxTokens) {
    auto *engine = reinterpret_cast<Engine *>(handle);
    if (!engine || !engine->model) {
        throwJava(env, "Lokal model yuklanmagan. Sozlamalardan model tanlang.");
        return nullptr;
    }
    const std::string userPrompt = fromJava(env, promptValue);
    // Qwen2.5-Instruct chat template. Shu bosqichdagi asosiy tavsiya etilgan model shu.
    const std::string prompt = "<|im_start|>system\nSen foydali, aniq va muloyim yordamchisan. Imkon qadar o'zbek tilida javob ber.\n<|im_end|>\n<|im_start|>user\n" + userPrompt + "\n<|im_end|>\n<|im_start|>assistant\n";

    // Har bir so'rovda kontekstni yangidan yaratish oldingi xabarlarning KV xotirasi aralashishini oldini oladi.
    if (engine->ctx) llama_free(engine->ctx);
    engine->ctx = llama_init_from_model(engine->model, engine->context_params);
    if (!engine->ctx) {
        throwJava(env, "Model kontekstini yaratib bo'lmadi. Telefon xotirasini bo'shating.");
        return nullptr;
    }

    const llama_vocab *vocab = llama_model_get_vocab(engine->model);
    std::vector<llama_token> promptTokens;
    if (!tokenize(vocab, prompt, promptTokens)) {
        throwJava(env, "Matnni model tokenlariga aylantirib bo'lmadi.");
        return nullptr;
    }
    if (promptTokens.size() > 1800) {
        throwJava(env, "Savol juda uzun. Qisqaroq savol yuboring.");
        return nullptr;
    }

    constexpr size_t batchCapacity = 512;
    llama_batch batch = llama_batch_init(static_cast<int32_t>(batchCapacity), 0, 1);
    bool promptFailed = false;
    for (size_t offset = 0; offset < promptTokens.size(); offset += batchCapacity) {
        const size_t count = std::min(batchCapacity, promptTokens.size() - offset);
        fillBatch(batch, promptTokens, offset, count);
        if (llama_decode(engine->ctx, batch) != 0) {
            promptFailed = true;
            break;
        }
    }
    if (promptFailed) {
        llama_batch_free(batch);
        throwJava(env, "Model savolni qayta ishlay olmadi.");
        return nullptr;
    }

    std::string output;
    const int limit = std::max(1, std::min(static_cast<int>(maxTokens), 192));
    int32_t position = static_cast<int32_t>(promptTokens.size());
    for (int i = 0; i < limit; ++i) {
        const llama_token token = greedyToken(engine);
        if (token < 0 || llama_vocab_is_eog(vocab, token)) break;
        std::vector<char> piece(512);
        int32_t pieceSize = llama_token_to_piece(vocab, token, piece.data(), static_cast<int32_t>(piece.size()), 0, true);
        if (pieceSize < 0) {
            piece.resize(static_cast<size_t>(-pieceSize));
            pieceSize = llama_token_to_piece(vocab, token, piece.data(), static_cast<int32_t>(piece.size()), 0, true);
        }
        if (pieceSize > 0) output.append(piece.data(), static_cast<size_t>(pieceSize));

        batch.n_tokens = 1;
        batch.token[0] = token;
        batch.pos[0] = position++;
        batch.n_seq_id[0] = 1;
        batch.seq_id[0][0] = 0;
        batch.logits[0] = true;
        if (llama_decode(engine->ctx, batch) != 0) break;
    }
    llama_batch_free(batch);
    return env->NewStringUTF(output.c_str());
}

extern "C" JNIEXPORT void JNICALL
Java_uz_studentai_mobile_local_LocalLlamaEngine_nativeFree(JNIEnv *, jobject, jlong handle) {
    delete reinterpret_cast<Engine *>(handle);
}
