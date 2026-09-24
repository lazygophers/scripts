package com.lazygophers.lazygit.action

import com.intellij.notification.NotificationGroupManager
import com.intellij.notification.NotificationType
import com.intellij.openapi.actionSystem.AnAction
import com.intellij.openapi.actionSystem.AnActionEvent
import com.intellij.openapi.actionSystem.CommonDataKeys
import com.intellij.openapi.actionSystem.PlatformDataKeys
import com.intellij.openapi.progress.ProgressManager
import com.intellij.openapi.vcs.VcsDataKeys
import com.intellij.vcs.commit.CommitWorkflowUi
import com.lazygophers.lazygit.ai.AiCommitClient
import com.lazygophers.lazygit.ai.SseConsumer
import com.lazygophers.lazygit.diff.IncludedChangesDiff
import com.lazygophers.lazygit.settings.LazyGitSettings
import java.lang.reflect.Method
import javax.swing.SwingUtilities

class GenerateCommitMessageAction : AnAction("AI 生成", "根据当前勾选变更生成 commit message", null) {
    override fun update(e: AnActionEvent) {
        e.presentation.isEnabled = e.getData(VcsDataKeys.COMMIT_WORKFLOW_UI) != null
    }

    override fun actionPerformed(e: AnActionEvent) {
        val project = e.project ?: return
        val settings = LazyGitSettings.getInstance()
        if (!settings.isAiReady()) {
            notify(project, "请先在 设置 > 工具 > Lazy Git 配置 AI、模型和 API Key", NotificationType.WARNING)
            com.intellij.openapi.options.ShowSettingsUtil.getInstance().showSettingsDialog(project, "Lazy Git")
            return
        }
        val ui = e.getData(VcsDataKeys.COMMIT_WORKFLOW_UI) ?: return
        val changes = ui.getIncludedChanges()
        val diff = IncludedChangesDiff.build(changes)
        if (diff.isBlank()) {
            notify(project, "当前没有勾选的变更", NotificationType.WARNING)
            return
        }
        val client = AiCommitClient(
            settings.api.protocol, settings.api.endpoint, settings.api.model,
            settings.apiKey() ?: return, settings.api.temperature, settings.api.connectTimeoutSeconds,
        )
        val prompt = "请根据以下 Git diff 生成一条 Conventional Commits 格式的 commit message。只输出一行，不要 markdown，不要解释。\n\n$diff"
        ProgressManager.getInstance().run(object : com.intellij.openapi.progress.Task.Backgroundable(project, "AI 生成 commit message", true) {
            private val generated = StringBuilder()
            override fun run(indicator: com.intellij.openapi.progress.ProgressIndicator) {
                try {
                    client.stream(prompt, object : SseConsumer {
                        override fun onDelta(text: String) {
                            if (indicator.isCanceled) throw java.util.concurrent.CancellationException()
                            generated.append(text)
                            indicator.text = generated.toString().takeLast(120)
                        }
                        override fun onRetryNoThinking() {
                            indicator.text = "模型不支持 thinking，正在重试"
                        }
                    })
                } catch (_: java.util.concurrent.CancellationException) {
                    return
                } catch (t: Throwable) {
                    SwingUtilities.invokeLater { notify(project, "AI 生成失败：${t.message ?: t.javaClass.simpleName}", NotificationType.ERROR) }
                    return
                }
                val message = generated.toString().trim()
                if (message.isNotEmpty() && !indicator.isCanceled) {
                    SwingUtilities.invokeLater {
                        if (!writeMessage(ui, message)) {
                            notify(project, "生成成功，但无法写入 commit message（IDE API 不兼容）", NotificationType.ERROR)
                        }
                    }
                }
            }
        })
    }

    /** 不同 2025.1 构建的 CommitWorkflowUi 消息编辑器访问器有差异，反射兼容。 */
    private fun writeMessage(ui: Any, message: String): Boolean {
        val candidates = listOf("setCommitMessage", "setMessage", "setText")
        for (name in candidates) {
            val method = ui.javaClass.methods.firstOrNull { it.name == name && it.parameterCount == 1 }
            if (method != null) return try { method.invoke(ui, message); true } catch (_: Exception) { false }
        }
        for (accessor in listOf("getCommitMessage", "getCommitMessageUi", "getEditor")) {
            val getter = ui.javaClass.methods.firstOrNull { it.name == accessor && it.parameterCount == 0 } ?: continue
            val target = try { getter.invoke(ui) } catch (_: Exception) { continue } ?: continue
            for (name in candidates) {
                val method = target.javaClass.methods.firstOrNull { it.name == name && it.parameterCount == 1 } ?: continue
                try { method.invoke(target, message); return true } catch (_: Exception) { }
            }
        }
        return false
    }

    private fun notify(project: com.intellij.openapi.project.Project, text: String, type: NotificationType) {
        NotificationGroupManager.getInstance().getNotificationGroup("Lazy Git")
            .createNotification(text, type).notify(project)
    }
}
