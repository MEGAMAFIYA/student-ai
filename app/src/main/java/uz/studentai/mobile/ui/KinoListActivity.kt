package uz.studentai.mobile.ui

import android.content.Intent
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import kotlinx.coroutines.launch
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ActivityKinoListBinding
import uz.studentai.mobile.network.ApiClient
import uz.studentai.mobile.network.MovieDto

/** KINO — kino katalogi (mavjud storage.py bazasidan) + tomosha qilish
 * (backend movie_watch.make_solo_stream_url orqali mavjud Watch Party
 * streaming mexanizmini qayta ishlatadi). */
class KinoListActivity : AppCompatActivity() {
    private lateinit var binding: ActivityKinoListBinding
    private lateinit var adapter: KinoAdapter

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityKinoListBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.setNavigationOnClickListener { finish() }

        adapter = KinoAdapter { movie -> openMovie(movie) }
        binding.movieList.layoutManager = LinearLayoutManager(this)
        binding.movieList.adapter = adapter

        binding.swipeRefresh.setOnRefreshListener {
            loadMovies(binding.searchInput.text?.toString().orEmpty())
        }

        binding.searchInput.setOnEditorActionListener { _: TextView, actionId: Int, event: KeyEvent? ->
            if (actionId == EditorInfo.IME_ACTION_SEARCH || actionId == EditorInfo.IME_ACTION_DONE ||
                (event != null && event.keyCode == KeyEvent.KEYCODE_ENTER)
            ) {
                loadMovies(binding.searchInput.text?.toString().orEmpty())
                true
            } else {
                false
            }
        }

        loadMovies("")
    }

    private fun loadMovies(query: String) {
        binding.swipeRefresh.isRefreshing = true
        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@KinoListActivity)
                val response = api.kinoList(query)
                if (response.isSuccessful) {
                    val movies = response.body()?.movies.orEmpty()
                    adapter.submit(movies)
                    binding.emptyText.visibility = if (movies.isEmpty()) View.VISIBLE else View.GONE
                } else if (response.code() == 401) {
                    Toast.makeText(this@KinoListActivity, R.string.error_unauthorized, Toast.LENGTH_LONG).show()
                } else {
                    Toast.makeText(this@KinoListActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(this@KinoListActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
            } finally {
                binding.swipeRefresh.isRefreshing = false
            }
        }
    }

    private fun openMovie(movie: MovieDto) {
        lifecycleScope.launch {
            try {
                val api = ApiClient.get(this@KinoListActivity)
                val response = api.kinoWatch(movie.id)
                val url = response.body()?.stream_url
                if (response.isSuccessful && url != null) {
                    val intent = Intent(this@KinoListActivity, KinoPlayerActivity::class.java)
                    intent.putExtra(KinoPlayerActivity.EXTRA_URL, url)
                    intent.putExtra(KinoPlayerActivity.EXTRA_TITLE, movie.title)
                    startActivity(intent)
                } else {
                    Toast.makeText(
                        this@KinoListActivity,
                        "Kino oqimi hozircha mavjud emas (server manzili sozlanmagan bo'lishi mumkin).",
                        Toast.LENGTH_LONG
                    ).show()
                }
            } catch (e: Exception) {
                Toast.makeText(this@KinoListActivity, R.string.error_network, Toast.LENGTH_SHORT).show()
            }
        }
    }
}
