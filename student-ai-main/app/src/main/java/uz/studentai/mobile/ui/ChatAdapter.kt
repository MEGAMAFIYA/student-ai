package uz.studentai.mobile.ui

import android.view.Gravity
import android.view.LayoutInflater
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.RecyclerView
import uz.studentai.mobile.R
import uz.studentai.mobile.databinding.ItemChatMessageBinding

data class ChatUiMessage(val role: String, val text: String) // role: "user" | "assistant"

class ChatAdapter(private val items: MutableList<ChatUiMessage>) : RecyclerView.Adapter<ChatAdapter.VH>() {

    class VH(val binding: ItemChatMessageBinding) : RecyclerView.ViewHolder(binding.root)

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): VH {
        val binding = ItemChatMessageBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return VH(binding)
    }

    override fun onBindViewHolder(holder: VH, position: Int) {
        val msg = items[position]
        val isUser = msg.role == "user"
        val context = holder.itemView.context

        holder.binding.messageRow.gravity = if (isUser) Gravity.END else Gravity.START
        holder.binding.bubbleText.text = msg.text
        holder.binding.bubbleText.setBackgroundResource(
            if (isUser) R.drawable.bg_bubble_user else R.drawable.bg_bubble_ai
        )
        holder.binding.bubbleText.setTextColor(
            ContextCompat.getColor(context, if (isUser) R.color.white else R.color.text_primary)
        )
    }

    override fun getItemCount() = items.size

    fun add(message: ChatUiMessage) {
        items.add(message)
        notifyItemInserted(items.size - 1)
    }
}
