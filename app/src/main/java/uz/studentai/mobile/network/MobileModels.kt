package uz.studentai.mobile.network

data class MobileTaskRequest(
    val feature: String,
    val text: String = "",
    val pages: Int? = null,
    val count: Int? = null,
    val target_lang: String? = null,
    val work_type: String? = null,
    val history: List<ChatMessageDto> = emptyList(),
)

data class MobileTaskResponse(
    val reply: String? = null,
    val text: String? = null,
    val file_base64: String? = null,
    val filename: String? = null,
    val mime: String? = null,
    val error: String? = null,
)

data class MobileFileItem(
    val name: String,
    val mime: String,
    val base64: String,
)

data class MobileFileTaskRequest(
    val feature: String,
    val instruction: String = "",
    val files: List<MobileFileItem> = emptyList(),
)
