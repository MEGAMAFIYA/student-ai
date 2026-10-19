package uz.studentai.mobile.network

import android.content.Context
import okhttp3.Interceptor
import okhttp3.Response
import org.json.JSONObject
import uz.studentai.mobile.util.AppLog
import java.io.IOException

/** Har bir server so'rovini (manzil, natija, davomiylik, xato sababi) jurnalga yozadi.
 * Maxfiylik: so'rovning query qismi (token/kod) va tanasi YOZILMAYDI. */
class LogInterceptor(private val context: Context) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val what = "${request.method} ${request.url.encodedPath.trimStart('/')}"
        val started = System.currentTimeMillis()
        val response: Response
        try {
            response = chain.proceed(request)
        } catch (e: IOException) {
            val sec = (System.currentTimeMillis() - started) / 1000.0
            AppLog.error(context, "Server", "$what — javob kelmadi (${"%.1f".format(sec)} s)", AppLog.explain(e))
            throw e
        }
        val sec = (System.currentTimeMillis() - started) / 1000.0
        val time = "%.1f".format(sec)
        if (response.isSuccessful) {
            AppLog.ok(context, "Server", "$what → ${response.code} ($time s)")
        } else {
            var serverError: String? = null
            try {
                val raw = response.peekBody(2048).string()
                serverError = JSONObject(raw).optString("error").takeIf { it.isNotBlank() }
            } catch (e: Exception) {
                // JSON emas (masalan HTML "Not Found") — e'tiborsiz.
            }
            AppLog.error(context, "Server", "$what → ${response.code} ($time s)", AppLog.explainHttp(response.code, serverError))
        }
        return response
    }
}
