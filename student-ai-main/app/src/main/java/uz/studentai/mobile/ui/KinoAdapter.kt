package uz.studentai.mobile.ui

import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import uz.studentai.mobile.databinding.ItemMovieRowBinding
import uz.studentai.mobile.network.MovieDto

class KinoAdapter(
    private val items: MutableList<MovieDto> = mutableListOf(),
    private val onClick: (MovieDto) -> Unit,
) : RecyclerView.Adapter<KinoAdapter.VH>() {

    class VH(val binding: ItemMovieRowBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemMovieRowBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val movie = items[position]
        holder.binding.movieTitle.text = movie.title
        holder.itemView.setOnClickListener { onClick(movie) }
    }

    override fun getItemCount() = items.size

    fun submit(newItems: List<MovieDto>) {
        items.clear()
        items.addAll(newItems)
        notifyDataSetChanged()
    }
}
