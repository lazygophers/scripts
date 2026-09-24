package com.lazygophers.lazygit.settings

import com.intellij.openapi.options.Configurable
import com.intellij.openapi.ui.ComboBox
import com.intellij.ui.components.JBCheckBox
import com.intellij.ui.components.JBTextField
import java.awt.Dimension
import java.awt.FlowLayout
import javax.swing.BoxLayout
import javax.swing.JButton
import javax.swing.JComponent
import javax.swing.JLabel
import javax.swing.JPanel
import javax.swing.JPasswordField
import javax.swing.SwingConstants
import javax.swing.SwingUtilities

/** 中文独立设置页。纯 Swing，避免 UI DSL 版本差异。 */
class LazyGitConfigurable : Configurable {

    private val aiEnabled = JBCheckBox("启用 AI 生成")
    private val protocol = ComboBox(arrayOf("openai", "anthropic"))
    private val endpoint = JBTextField()
    private val model = JBTextField()
    private val apiKey = JPasswordField()
    private val temperature = JBTextField()
    private val timeout = JBTextField()
    private val inlineEnabled = JBCheckBox("启用行内信息（点击行显示 Git 提交信息）")
    private val testResult = JLabel(" ")

    private val panel: JPanel = JPanel().apply {
        layout = BoxLayout(this, BoxLayout.Y_AXIS)
    }

    private fun row(label: String, field: JComponent) {
        panel.add(JPanel().apply {
            layout = FlowLayout(FlowLayout.LEFT)
            add(JLabel(label, null, SwingConstants.LEADING))
            add(field)
            field.preferredSize = Dimension(360, field.preferredSize.height)
        })
    }

    init {
        row("启用 AI 生成：", aiEnabled)
        row("协议：", protocol)
        row("Endpoint（base URL）：", endpoint)
        row("模型：", model)
        row("API Key（存于 IDE 密码库）：", apiKey)
        row("Temperature：", temperature)
        row("连接超时（秒）：", timeout)
        panel.add(inlineEnabled)
        panel.add(JPanel().apply {
            layout = FlowLayout(FlowLayout.LEFT)
            add(JButton("测试连接").apply {
                addActionListener {
                    testResult.text = "测试中…"
                    Thread({
                        val msg = try {
                            if (runTestConnection()) "成功" else "失败（返回内容为空）"
                        } catch (e: Exception) {
                            "失败：${e.message ?: e.javaClass.simpleName}"
                        }
                        SwingUtilities.invokeLater { testResult.text = "测试连接：$msg" }
                    }).apply { isDaemon = true }.start()
                }
            })
            add(testResult)
        })
    }

    private fun runTestConnection(): Boolean {
        val s = currentSettings()
        val key = String(apiKey.password)
        if (s.endpoint.isBlank() || s.model.isBlank() || key.isBlank()) return false
        val client = com.lazygophers.lazygit.ai.AiCommitClient(
            protocol = s.protocol, endpoint = s.endpoint, model = s.model,
            apiKey = key, temperature = s.temperature, connectTimeoutSeconds = s.connectTimeoutSeconds,
        )
        val out = StringBuilder()
        client.stream("只回复两个字母：ok", object : com.lazygophers.lazygit.ai.SseConsumer {
            override fun onDelta(t: String) { out.append(t) }
            override fun onRetryNoThinking() {}
        })
        return out.isNotBlank()
    }

    private fun currentSettings(): LazyGitSettings.State {
        val s = LazyGitSettings.State()
        s.aiEnabled = aiEnabled.isSelected
        s.protocol = protocol.selectedItem as? String ?: s.protocol
        s.endpoint = endpoint.text.trim()
        s.model = model.text.trim()
        s.temperature = temperature.text.trim().toDoubleOrNull() ?: 0.3
        s.connectTimeoutSeconds = timeout.text.trim().toIntOrNull() ?: 15
        s.inlineEnabled = inlineEnabled.isSelected
        return s
    }

    override fun getDisplayName(): String = "Lazy Git"

    override fun createComponent(): JComponent = panel

    override fun isModified(): Boolean {
        val s = currentSettings()
        val cur = LazyGitSettings.getInstance().api
        val keyChanged = String(apiKey.password) != (LazyGitSettings.getInstance().apiKey() ?: "")
        return s != cur || keyChanged
    }

    override fun apply() {
        val inst = LazyGitSettings.getInstance()
        inst.loadState(currentSettings())
        inst.saveApiKey(String(apiKey.password))
    }

    override fun reset() {
        val s = LazyGitSettings.getInstance().api
        aiEnabled.isSelected = s.aiEnabled
        protocol.selectedItem = s.protocol
        endpoint.text = s.endpoint
        model.text = s.model
        temperature.text = s.temperature.toString()
        timeout.text = s.connectTimeoutSeconds.toString()
        inlineEnabled.isSelected = s.inlineEnabled
        apiKey.text = LazyGitSettings.getInstance().apiKey() ?: ""
        testResult.text = " "
    }
}
