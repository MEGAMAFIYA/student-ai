package uz.studentai.mobile.ui

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import android.speech.RecognizerIntent
import android.speech.tts.TextToSpeech
import android.view.View
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uz.studentai.mobile.R
import uz.studentai.mobile.assistant.AssistantDeviceActions
import uz.studentai.mobile.databinding.ActivityChatBinding
import uz.studentai.mobile.local.LocalLlamaEngine
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.AssistantPlanRequest
import uz.studentai.mobile.network.ChatMessageDto
import uz.studentai.mobile.network.ChatRequest
import uz.studentai.mobile.util.SessionManager
import java.util.Locale

/** AL Yordamchi chat: AI rejasini serverdan oladi, telefon amallarini faqat Android bajaradi. */
class ChatActivity : AppCompatActivity(), TextToSpeech.OnInitListener {
    private lateinit var binding: ActivityChatBinding
    private val messages = mutableListOf<ChatUiMessage>()
    private lateinit var adapter: ChatAdapter
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityChatBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.title = "AL Yordamchi"
        binding.toolbar.setNavigationOnClickListener { finish() }
        adapter = ChatAdapter(messages)
        binding.messagesList.layoutManager = LinearLayoutManager(this)
        binding.messagesList.adapter = adapter
        binding.sendButton.setOnClickListener { send() }
        binding.voiceButton.setOnClickListener { startVoiceInput() }
        tts = TextToSpeech(this, this)
        adapter.add(ChatUiMessage("assistant", "Salom! Men AL Yordamchiman. Buyruqni yozing yoki mikrofon orqali ayting. Masalan: ‘Soat 7:00 ga budilnik qo‘y’."))
    }

    private fun send() {
        val text = binding.messageInput.text?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        binding.messageInput.setText("")
        adapter.add(ChatUiMessage("user", text))
        binding.messagesList.scrollToPosition(messages.size - 1)
        binding.typingProgress.visibility = View.VISIBLE
        binding.sendButton.isEnabled = false
        binding.voiceButton.isEnabled = false
        val history = messages.dropLast(1).takeLast(10).map { ChatMessageDto(it.role, it.text) }

        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@ChatActivity)
                val response = api.assistantPlan(AssistantPlanRequest(text, history))
                if (response.isSuccessful && response.body() != null) {
                    val plan = response.body()!!
                    if (plan.action == "none") {
                        addAssistantMessage(plan.reply.ifBlank { "Tushundim." }, speak = true)
                    } else {
                        // Device actions run locally; the model cannot call arbitrary Android APIs.
                        AssistantDeviceActions.execute(this@ChatActivity, plan.action, plan.data, plan.reply) { result ->
                            runOnUiThread { addAssistantMessage(result, speak = true) }
                        }
                    }
                } else if (response.code() == 401) {
                    handleUnauthorized()
                } else {
                    // Backward compatibility with servers that have not yet deployed the AL endpoint.
                    val fallback = api.chat(ChatRequest(message = text, history = history))
                    addAssistantMessage(fallback.body()?.reply ?: getString(R.string.error_network), speak = true)
                }
            } catch (e: Exception) {
                // If the online assistant is unavailable, retain the existing on-device model path.
                try {
                    val localModel = LocalLlamaEngine.selectedModel(this@ChatActivity)
                    if (localModel != null) {
                        val context = messages.dropLast(1).takeLast(8).joinToString("\n") {
                            if (it.role == "assistant") "Yordamchi: ${it.text}" else "Foydalanuvchi: ${it.text}"
                        }
                        val reply = withContext(Dispatchers.IO) {
                            LocalLlamaEngine.generate(this@ChatActivity, listOf(context, "Foydalanuvchi: $text").filter { it.isNotBlank() }.joinToString("\n"))
                        }
                        addAssistantMessage("(Lokal AI) $reply", speak = true)
                    } else addAssistantMessage("Aloqa xatosi: ${e.localizedMessage ?: getString(R.string.error_network)}")
                } catch (localError: Exception) {
                    addAssistantMessage("AI bilan bog‘lanib bo‘lmadi. Internet va server holatini tekshiring.")
                }
            } finally {
                binding.typingProgress.visibility = View.GONE
                binding.sendButton.isEnabled = true
                binding.voiceButton.isEnabled = true
                binding.messagesList.scrollToPosition(messages.size - 1)
            }
        }
    }

    private fun addAssistantMessage(text: String, speak: Boolean = false) {
        adapter.add(ChatUiMessage("assistant", text))
        binding.messagesList.scrollToPosition(messages.size - 1)
        if (speak && ttsReady) tts?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "al_reply_${System.currentTimeMillis()}")
    }

    private fun startVoiceInput() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            Toast.makeText(this, "Ovozli buyruq uchun mikrofon ruxsatini yoqing.", Toast.LENGTH_LONG).show()
            return
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "uz-UZ")
            putExtra(RecognizerIntent.EXTRA_PROMPT, "AL Yordamchiga buyruq bering")
        }
        try { startActivityForResult(intent, REQUEST_SPEECH) }
        catch (_: Exception) { Toast.makeText(this, "Ovoz tanish xizmati topilmadi.", Toast.LENGTH_LONG).show() }
    }

    @Deprecated("Uses system speech recognizer activity")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQUEST_SPEECH && resultCode == RESULT_OK) {
            val spoken = data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull().orEmpty()
            if (spoken.isNotBlank()) { binding.messageInput.setText(spoken); send() }
        }
    }

    override fun onInit(status: Int) {
        ttsReady = status == TextToSpeech.SUCCESS
        if (ttsReady) tts?.language = Locale("uz", "UZ")
    }

    private fun handleUnauthorized() {
        Toast.makeText(this, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
        SessionManager(this).clear()
        startActivity(Intent(this, LoginActivity::class.java))
        finish()
    }

    override fun onDestroy() {
        tts?.stop()
        tts?.shutdown()
        super.onDestroy()
    }

    companion object { private const val REQUEST_SPEECH = 7412 }
}
