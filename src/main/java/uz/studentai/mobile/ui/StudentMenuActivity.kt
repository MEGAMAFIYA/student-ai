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
            uz.studentai.mobile.util.AppLog.info(this, "Menyu", "Ochildi: ${item.label}")
            when (item.key) {
                "universal" -> startActivity(Intent(this, ChatActivity::class.java))
                "quiz" -> startActivity(Intent(this, QuizSetupActivity::class.java))
                "translate" -> openSimpleTool(SimpleToolKind.TRANSLATE)
                "grammar" -> openSimpleTool(SimpleToolKind.GRAMMAR)
                "summarize" -> openSimpleTool(SimpleToolKind.SUMMARIZE)
                "solve" -> openSimpleTool(SimpleToolKind.SOLVE)
                "citation" -> startActivity(Intent(this, CitationActivity::class.java))
                else -> {
                    val i = Intent(this, FeatureActivity::class.java)
                    i.putExtra(FeatureActivity.EXTRA_FEATURE, item.key)
                    i.putExtra(FeatureActivity.EXTRA_TITLE, item.label)
                    startActivity(i)
                }
            }
        }
    }

    private fun openSimpleTool(kind: SimpleToolKind) {
        val intent = Intent(this, SimpleAiToolActivity::class.java)
        intent.putExtra(SimpleAiToolActivity.EXTRA_KIND, kind.name)
        startActivity(intent)
    }
}
