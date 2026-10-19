package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import uz.studentai.mobile.local.LocalLlamaEngine
import androidx.recyclerview.widget.LinearLayoutManager
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityChatBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.ChatMessageDto
import uz.studentai.mobile.network.ChatRequest
import uz.studentai.mobile.util.SessionManager

/** STUDENT universal chat. Tanlangan GGUF model mavjud bo'lsa lokal ishlaydi;
 * model tanlanmagan bo'lsa avvalgi backend /api/mobile/chat ishlatiladi. */
class ChatActivity : AppCompatActivity() {
    private lateinit var binding: ActivityChatBinding
    private val messages = mutableListOf<ChatUiMessage>()
    private lateinit var adapter: ChatAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        adapter = ChatAdapter(messages)
        binding.messagesList.layoutManager = LinearLayoutManager(this)
        binding.messagesList.adapter = adapter

        binding.sendButton.setOnClickListener { send() }
    }

    private fun send() {
        val text = binding.messageInput.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        binding.messageInput.setText("")

        adapter.add(ChatUiMessage("user", text))
        binding.messagesList.scrollToPosition(messages.size - 1)
        binding.typingProgress.visibility = View.VISIBLE
        binding.sendButton.isEnabled = false

        // Suhbat tarixi (oxirgi xabardan OLDINGI barcha xabarlar) — server
        // shu asosida kontekstni tushunadi.
        val history = messages.dropLast(1).map { ChatMessageDto(it.role, it.text) }

        lifecycleScope.launch {
            try {
                val localModel = LocalLlamaEngine.selectedModel(this@ChatActivity)
                if (localModel != null) {
                    uz.studentai.mobile.util.AppLog.info(this@ChatActivity, "Chat", "Lokal model orqali javob olinmoqda (internetsiz).")
                    // Model tanlangan bo'lsa, internet/serverga umuman murojaat qilmaymiz.
                    val recentContext = messages.dropLast(1).takeLast(8).joinToString("\n") {
                        if (it.role == "assistant") "Yordamchi: ${it.text}" else "Foydalanuvchi: ${it.text}"
                    }
                    val localPrompt = listOf(recentContext, "Foydalanuvchi: $text")
                        .filter { it.isNotBlank() }.joinToString("\n")
                    val reply = withContext(Dispatchers.IO) {
                        LocalLlamaEngine.generate(this@ChatActivity, localPrompt)
                    }
                    adapter.add(ChatUiMessage("assistant", reply))
                    uz.studentai.mobile.util.AppLog.ok(this@ChatActivity, "Chat", "Lokal model javob berdi.")
                } else {
                    // Model tanlanmagan bo'lsa, avvalgi server rejimi o'z holicha qoladi.
                    val api = ApiClient.get(this@ChatActivity)
                    val response = api.chat(ChatRequest(message = text, history = history))
                    when {
                        response.isSuccessful && response.body()?.reply != null -> {
                            adapter.add(ChatUiMessage("assistant", response.body()!!.reply!!))
                        }
                        response.code() == 401 -> handleUnauthorized()
                        else -> uz.studentai.mobile.util.AppLog.error(this@ChatActivity, "Chat", "Universal chat javob bermadi", uz.studentai.mobile.util.AppLog.explainHttp(response.code(), null))
                    }
                    when {
                        response.isSuccessful && response.body()?.reply != null -> Unit
                        response.code() == 401 -> Unit
                        else -> adapter.add(ChatUiMessage("assistant", getString(R.string.error_network)))
                    }
                }
            } catch (e: Exception) {
                val message = e.message?.takeIf { it.isNotBlank() } ?: getString(R.string.error_network)
                uz.studentai.mobile.util.AppLog.error(this@ChatActivity, "Chat", "Chatda xatolik", uz.studentai.mobile.util.AppLog.explain(e))
                adapter.add(ChatUiMessage("assistant", "Xatolik: $message"))
            } catch (e: LinkageError) {
                adapter.add(ChatUiMessage("assistant", "Lokal AI dvigateli yuklanmadi. APKning yangi versiyasini o'rnating."))
            } finally {
                binding.typingProgress.visibility = View.GONE
                binding.sendButton.isEnabled = true
                binding.messagesList.scrollToPosition(messages.size - 1)
            }
        }
    }

    private fun handleUnauthorized() {
        Toast.makeText(this, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
        SessionManager(this).clear()
        startActivity(Intent(this, LoginActivity::class.java))
        finish()
    }
}
