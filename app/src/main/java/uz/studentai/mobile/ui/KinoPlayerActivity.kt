package uz.studentai.mobile.ui

import android.net.Uri
import android.os.Bundle
import android.view.View
import android.widget.MediaController
import androidx.appcompat.app.AppCompatActivity
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityKinoPlayerBinding

/** Kino tomosha qilish — VideoView orqali (Android'ning o'zi HTTP Range
 * so'rovlarini qo'llab-quvvatlaydi, shuning uchun server tomonidagi
 * mavjud Range-streaming, o'zgarishsiz, to'g'ridan-to'g'ri ishlaydi). */
class KinoPlayerActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityKinoPlayerBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.toolbar.title = intent.getStringExtra(EXTRA_TITLE) ?: getString(R.string.home_kino)
        binding.toolbar.setNavigationOnClickListener { finish() }

        val url = intent.getStringExtra(EXTRA_URL)
        if (url.isNullOrBlank()) {
            showError()
            return
        }

        val controller = MediaController(this)
        controller.setAnchorView(binding.videoView)
        binding.videoView.setMediaController(controller)
        binding.videoView.setVideoURI(Uri.parse(url))
        binding.videoView.setOnPreparedListener {
            binding.playerLoading.visibility = View.GONE
            binding.videoView.start()
        }
        binding.videoView.setOnErrorListener { _, _, _ ->
            showErrorOn(binding)
            true
        }
    }

    private fun showError() {
        // ActivityKinoPlayerBinding hali yaratilmagan holatda ham
        // ishlashi uchun umumiy Toast bilan cheklanamiz.
        android.widget.Toast.makeText(this, getString(R.string.error_network), android.widget.Toast.LENGTH_LONG).show()
        finish()
    }

    private fun showErrorOn(binding: ActivityKinoPlayerBinding) {
        binding.playerLoading.visibility = View.GONE
        binding.playerError.visibility = View.VISIBLE
        binding.playerError.text = getString(R.string.error_network)
    }

    companion object {
        const val EXTRA_URL = "extra_url"
        const val EXTRA_TITLE = "extra_title"
    }
}
