package uz.studentai.mobile.network

import android.content.Context
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import uz.studentai.mobile.BuildConfig
import uz.studentai.mobile.util.SessionManager
import java.util.concurrent.TimeUnit

/**
 * Bitta umumiy Retrofit/OkHttp instansiyasi. Har bir so'rovga avtomatik
 * ravishda "Authorization: Bearer <session_token>" header'i qo'shiladi
 * (agar foydalanuvchi kirgan bo'lsa — mobile_auth.py shu tokenni tekshiradi).
 */
object ApiClient {
    @Volatile
    private var retrofit: Retrofit? = null

    fun get(context: Context): ApiService {
        val instance = retrofit ?: synchronized(this) {
            retrofit ?: build(context.applicationContext).also { retrofit = it }
        }
        return instance.create(ApiService::class.java)
    }

    private fun build(context: Context): Retrofit {
        val session = SessionManager(context)

        val authInterceptor = Interceptor { chain ->
            val requestBuilder = chain.request().newBuilder()
            session.sessionToken?.let { token ->
                requestBuilder.addHeader("Authorization", "Bearer $token")
            }
            chain.proceed(requestBuilder.build())
        }

        val logging = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) HttpLoggingInterceptor.Level.BASIC else HttpLoggingInterceptor.Level.NONE
        }

        val client = OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .addInterceptor(logging)
            .connectTimeout(20, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS) // AI javobi biroz vaqt olishi mumkin
            .writeTimeout(20, TimeUnit.SECONDS)
            .build()

        return Retrofit.Builder()
            .baseUrl(BuildConfig.BASE_URL) // oxirida "/" bo'lishi SHART (build.gradle.kts'ga qarang)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }
}
