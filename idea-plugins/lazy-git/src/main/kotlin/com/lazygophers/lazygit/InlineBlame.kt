package com.lazygophers.lazygit

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ReadAction
import com.intellij.openapi.components.Service
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.EditorFactory
import com.intellij.openapi.editor.EditorLinePainter
import com.intellij.openapi.editor.LineExtensionInfo
import com.intellij.openapi.editor.event.CaretEvent
import com.intellij.openapi.editor.event.CaretListener
import com.intellij.openapi.editor.event.EditorMouseEvent
import com.intellij.openapi.editor.event.EditorMouseListener
import com.intellij.openapi.editor.markup.TextAttributes
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.util.Key
import com.intellij.openapi.vcs.annotate.FileAnnotation
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.ui.JBColor
import git4idea.GitVcs
import java.awt.Font
import java.text.SimpleDateFormat
import java.util.Date
import java.util.concurrent.ConcurrentHashMap

data class LineBlame(val author: String, val time: String, val message: String) {
    fun display(): String = " $author · $time · $message"
}

object BlameFormatter {
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd")

    /** 任一要素缺失（未提交行/无作者/空 message）返回 null = 该行不显示。 */
    fun format(author: String?, date: Date?, message: String?): LineBlame? {
        val a = author?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        val d = date ?: return null
        val m = message?.lineSequence()?.firstOrNull { it.isNotBlank() }?.trim()?.takeIf { it.isNotEmpty() }
            ?: return null
        return LineBlame(a, dateFormat.format(d), m)
    }
}

val BLAME_LINE: Key<Int> = Key.create<Int>("lazygit.blameLine")

@Service(Service.Level.PROJECT)
class InlineBlameService(private val project: Project) {

    /** key = 路径:modificationStamp；空 map = 该文件 blame 不可得（无 Git/新文件），负缓存。 */
    private val cache = ConcurrentHashMap<String, Map<Int, LineBlame>>()
    private val pending = ConcurrentHashMap.newKeySet<String>()

    fun blameFor(file: VirtualFile, line: Int): LineBlame? {
        val key = "${file.path}:${file.modificationStamp}"
        val map = cache[key]
        if (map != null) return map[line]
        requestBlame(file, key)
        return null
    }

    private fun requestBlame(file: VirtualFile, key: String) {
        if (!pending.add(key)) return
        ApplicationManager.getApplication().executeOnPooledThread {
            val map = try {
                annotateAll(file)
            } catch (e: Exception) {
                emptyMap()
            }
            cache[key] = map
            pending.remove(key)
            ApplicationManager.getApplication().invokeLater {
                FileEditorManager.getInstance(project).selectedTextEditor?.contentComponent?.repaint()
            }
        }
    }

    private fun annotateAll(file: VirtualFile): Map<Int, LineBlame> {
        val provider = GitVcs.getInstance(project)?.annotationProvider ?: return emptyMap()
        // annotate 读 VFS/文档，必须持读锁；后台线程 + ReadAction 是标准组合
        val annotation: FileAnnotation? = ReadAction.compute<FileAnnotation, Exception> {
            try {
                provider.annotate(file)
            } catch (e: Exception) {
                null
            }
        }
        if (annotation == null) return emptyMap()
        val details = HashMap<String, Pair<String, String?>>() // revision asString -> (author, message)
        val revisions = annotation.revisions ?: return emptyMap()
        for (rev in revisions) {
            val author = try { rev.author ?: "" } catch (e: Exception) { "" }
            val message = try { rev.commitMessage } catch (e: Exception) { null }
            details[rev.revisionNumber.asString()] = author to message
        }
        val result = HashMap<Int, LineBlame>()
        for (line in 0 until annotation.lineCount) {
            val rev = annotation.getLineRevisionNumber(line) ?: continue // 未提交行
            val (author, message) = details[rev.asString()] ?: continue
            BlameFormatter.format(author, annotation.getLineDate(line), message)?.let { result[line] = it }
        }
        return result
    }
}

/** 光标/点击跟踪：监听 EditorFactory 事件多路广播器，随 project 注销。 */
@Service(Service.Level.PROJECT)
class InlineBlameTrigger(private val project: Project) {

    init {
        val multicaster = EditorFactory.getInstance().eventMulticaster
        val onPosition = { editor: Editor, line: Int ->
            if (editor.project == project) {
                editor.putUserData(BLAME_LINE, line)
                val file = FileDocumentManager.getInstance().getFile(editor.document)
                if (file != null && file.isInLocalFileSystem) {
                    project.getService(InlineBlameService::class.java).blameFor(file, line)
                }
                editor.contentComponent.repaint()
            }
        }
        multicaster.addCaretListener(object : CaretListener {
            override fun caretPositionChanged(event: CaretEvent) {
                onPosition(event.editor, event.newPosition.line)
            }
        }, project)
        multicaster.addEditorMouseListener(object : EditorMouseListener {
            override fun mouseClicked(event: EditorMouseEvent) {
                onPosition(event.editor, event.editor.xyToLogicalPosition(event.mouseEvent.point).line)
            }
        }, project)
    }
}

class InlineBlamePainter : EditorLinePainter() {

    override fun getLineExtensions(
        project: Project,
        file: VirtualFile,
        editorLineIndex: Int,
    ): Collection<LineExtensionInfo>? {
        // projectService 懒加载：第一次绘制时把光标监听器挂上，否则 BLAME_LINE 永远没值
        project.getService(InlineBlameTrigger::class.java)
        if (!com.lazygophers.lazygit.settings.LazyGitSettings.getInstance().api.inlineEnabled) return null
        val editor = FileEditorManager.getInstance(project).selectedTextEditor ?: return null
        if (FileDocumentManager.getInstance().getFile(editor.document) != file) return null
        if (editor.getUserData(BLAME_LINE) != editorLineIndex) return null
        val blame = project.getService(InlineBlameService::class.java).blameFor(file, editorLineIndex)
            ?: return null
        return listOf(
            LineExtensionInfo(
                blame.display(),
                TextAttributes().apply {
                    foregroundColor = JBColor.GRAY
                    setFontType(Font.ITALIC)
                },
            ),
        )
    }
}
