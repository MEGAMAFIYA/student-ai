package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import uz.studentai.mobile.databinding.ActivityStudentMenuBinding

class StudentMenuActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val binding = ActivityStudentMenuBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        binding.menuList.layoutManager = LinearLayoutManager(this)
        binding.menuList.adapter = StudentMenuAdapter(STUDENT_MENU_ITEMS) { item ->
            when (item.key) {
                "universal" -> startActivity(Intent(this, ChatActivity::class.java))
                "quiz" -> startActivity(Intent(this, QuizSetupActivity::class.java))
                else -> {
                    val i = Intent(this, ComingSoonActivity::class.java)
                    i.putExtra(ComingSoonActivity.EXTRA_TITLE, item.label)
                    startActivity(i)
                }
            }
        }
    }
}
