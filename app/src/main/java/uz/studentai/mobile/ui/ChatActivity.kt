package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityChatBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.ChatMessageDto
import uz.studentai.mobile.network.ChatRequest
import uz.studentai.mobile.util.SessionManager

/** STUDENT > 💬 UNIVERSAL CHAT — botdagi bilan bir xil AI (ask_ai) bilan
 * suhbat, backend /api/mobile/chat orqali. */
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
                val api = ApiClient.get(this@ChatActivity)
                val response = api.chat(ChatRequest(message = text, history = history))
                when {
                    response.isSuccessful && response.body()?.reply != null -> {
                        adapter.add(ChatUiMessage("assistant", response.body()!!.reply!!))
                    }
                    response.code() == 401 -> handleUnauthorized()
                    else -> adapter.add(ChatUiMessage("assistant", getString(R.string.error_network)))
                }
            } catch (e: Exception) {
                adapter.add(ChatUiMessage("assistant", getString(R.string.error_network)))
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
