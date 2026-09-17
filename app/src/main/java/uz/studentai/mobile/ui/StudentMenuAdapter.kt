package uz.studentai.mobile.ui

import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import androidx.recyclerview.widget.RecyclerView
import uz.studentai.mobile.databinding.ItemMenuRowBinding

class StudentMenuAdapter(
    private val items: List<StudentMenuItem>,
    private val onClick: (StudentMenuItem) -> Unit,
) : RecyclerView.Adapter<StudentMenuAdapter.VH>() {

    class VH(val binding: ItemMenuRowBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemMenuRowBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val item = items[position]
        holder.binding.rowLabel.text = item.label
        holder.binding.rowBadge.visibility = if (item.implemented) View.GONE else View.VISIBLE
        holder.itemView.setOnClickListener { onClick(item) }
    }

    override fun getItemCount() = items.size
}
