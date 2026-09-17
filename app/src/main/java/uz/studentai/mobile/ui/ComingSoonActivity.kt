package uz.studentai.mobile.ui

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import uz.studentai.mobile.databinding.ActivityComingSoonBinding

/** 2, 3-bosqichlarda ulanadigan funksiyalar uchun vaqtinchalik ekran. */
class ComingSoonActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityComingSoonBinding.inflate(layoutInflater)
        setContentView(binding.root)

        intent.getStringExtra(EXTRA_TITLE)?.let { binding.titleText.text = it }
        binding.backButton.setOnClickListener { finish() }
    }

    companion object {
        const val EXTRA_TITLE = "extra_title"
    }
}
