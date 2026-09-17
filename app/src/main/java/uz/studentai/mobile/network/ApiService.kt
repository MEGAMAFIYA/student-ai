package uz.studentai.mobile.network

import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * 📱 Backend (mobile_api.py, Telegram bot bilan BIR XIL serverda) bilan
 * gaplashadigan Retrofit interfeysi. Har bir endpoint mobile_api.py'dagi
 * mos yo'l bilan 1:1 mos keladi.
 */

// ---- Auth ----
data class AuthStartResponse(
    val login_token: String,
    val deep_link: String,
    val expires_in: Int,
)

data class UserDto(
    val id: Long,
    val username: String? = null,
    val first_name: String? = null,
)

data class PollResponse(
    val status: String, // "pending" | "ok" | "expired"
    val session_token: String? = null,
    val user: UserDto? = null,
)

// ---- Chat ----
data class ChatMessageDto(val role: String, val content: String)
data class ChatRequest(val message: String, val history: List<ChatMessageDto> = emptyList())
data class ChatResponse(val reply: String? = null, val error: String? = null)

// ---- Quiz ----
data class QuizStartRequest(val n: Int? = null, val pro: Boolean = false, val jas: Boolean = false)

data class QuizQuestionDto(
    val index: Int,
    val total: Int,
    val topic: String,
    val question: String,
    val options: List<String>? = null, // null bo'lsa — Pro rejim (variantlarsiz)
)

data class QuizQuestionResponse(
    val finished: Boolean,
    val question: QuizQuestionDto? = null,
    val error: String? = null,
)

data class QuizAnswerRequest(val index: Int? = null, val text: String? = null)

data class QuizStatsDto(val total: Int, val ok: Int, val bad: Int)

data class QuizAnswerResponse(
    val correct: Boolean,
    val correct_answer: String? = null,
    val finished: Boolean,
    val next: QuizQuestionDto? = null,
    val stats: QuizStatsDto? = null,
    val error: String? = null,
)

// ---- Kino ----
data class MovieDto(val id: String, val title: String, val size: Long)
data class KinoListResponse(val movies: List<MovieDto> = emptyList(), val error: String? = null)
data class KinoWatchResponse(val stream_url: String? = null, val error: String? = null)

interface ApiService {

    @POST("api/mobile/auth/start")
    suspend fun authStart(): AuthStartResponse

    @GET("api/mobile/auth/poll")
    suspend fun authPoll(@Query("token") token: String): PollResponse

    @POST("api/mobile/chat")
    suspend fun chat(@Body body: ChatRequest): Response<ChatResponse>

    @POST("api/mobile/quiz/start")
    suspend fun quizStart(@Body body: QuizStartRequest): Response<QuizQuestionResponse>

    @POST("api/mobile/quiz/answer")
    suspend fun quizAnswer(@Body body: QuizAnswerRequest): Response<QuizAnswerResponse>

    @GET("api/mobile/kino/list")
    suspend fun kinoList(@Query("q") q: String = ""): Response<KinoListResponse>

    @GET("api/mobile/kino/watch/{id}")
    suspend fun kinoWatch(@Path("id") id: String): Response<KinoWatchResponse>

    @POST("api/mobile/task")
    suspend fun task(@Body body: MobileTaskRequest): Response<MobileTaskResponse>

    @POST("api/mobile/file-task")
    suspend fun fileTask(@Body body: MobileFileTaskRequest): Response<MobileTaskResponse>
}
