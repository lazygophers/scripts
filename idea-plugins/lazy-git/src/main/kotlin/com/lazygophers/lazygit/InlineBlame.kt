package com.lazygophers.lazygit

import com.intellij.openapi.application.ApplicationManager
import com.intellij.openapi.application.ReadAction
import com.intellij.openapi.components.Service
import com.intellij.openapi.diagnostic.Logger
import com.intellij.openapi.editor.Editor
import com.intellij.openapi.editor.EditorCustomElementRenderer
import com.intellij.openapi.editor.EditorFactory
import com.intellij.openapi.editor.Inlay
import com.intellij.openapi.editor.LineExtensionInfo
import com.intellij.openapi.editor.event.CaretEvent
import com.intellij.openapi.editor.event.CaretListener
import com.intellij.openapi.editor.event.EditorMouseEvent
import com.intellij.openapi.editor.event.EditorMouseListener
import com.intellij.openapi.editor.markup.TextAttributes
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.fileEditor.FileEditorManager
import com.intellij.openapi.project.Project
import com.intellij.openapi.startup.StartupActivity
import com.intellij.openapi.util.Key
import com.intellij.openapi.vcs.annotate.FileAnnotation
import com.intellij.openapi.vfs.VirtualFile
import com.intellij.ui.JBColor
import git4idea.GitVcs
import java.awt.Font
import java.text.SimpleDateFormat
import java.util.Date
import java.util.concurrent.ConcurrentHashMap

private val LOG = Logger.getInstance("com.lazygophers.lazygit")
private val BLAME_INLAY: Key<Inlay<*>?> = Key.create("lazygit.blameInlay")
private val BLAME_FONT = Font("SansSerif", Font.ITALIC, 12)

data class LineBlame(val author: String, val time: String, val message: String) {
    fun display(): String = " $author · $time · $message"
}

object BlameFormatter {
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd")

    fun format(author: String?, date: Date?, message: String?): LineBlame? {
        val name = author?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        val day = date ?: return null
        val firstLine = message?.lineSequence()?.firstOrNull { it.isNotBlank() }?.trim()
            ?.takeIf { it.isNotEmpty() } ?: return null
        return LineBlame(name, dateFormat.format(day), firstLine)
    }
}

@Service(Service.Level.PROJECT)
class InlineBlameService(private val project: Project) {
    private val cache = ConcurrentHashMap<String, Map<Int, LineBlame>>()
    private val pending = ConcurrentHashMap.newKeySet<String>()

    fun blameFor(file: VirtualFile, line: Int, ready: (LineBlame?) -> Unit = {}) {
        val key = "${file.path}:${file.modificationStamp}"
        cache[key]?.let { ready(it[line]); return }
        if (!pending.add(key)) return
        ApplicationManager.getApplication().executeOnPooledThread {
            val map = try { annotateAll(file) } catch (e: Exception) {
                LOG.warn("blame 失败: ${file.path}", e)
                emptyMap()
            }
            cache[key] = map
            pending.remove(key)
            ApplicationManager.getApplication().invokeLater { ready(map[line]) }
        }
    }

    private fun annotateAll(file: VirtualFile): Map<Int, LineBlame> {
        val provider = GitVcs.getInstance(project)?.annotationProvider ?: return emptyMap()
        val annotation = ReadAction.compute<FileAnnotation?, Exception> {
            try { provider.annotate(file) } catch (e: Exception) {
                LOG.warn("annotate 失败: ${file.path}", e)
                null
            }
        } ?: return emptyMap()
        val revisions = annotation.revisions ?: return emptyMap()
        val details = revisions.associate {
            it.revisionNumber.asString() to ((it.author ?: "") to it.commitMessage)
        }
        return buildMap {
            for (line in 0 until annotation.lineCount) {
                val revision = annotation.getLineRevisionNumber(line) ?: continue
                val (author, message) = details[revision.asString()] ?: continue
                BlameFormatter.format(author, annotation.getLineDate(line), message)?.let { put(line, it) }
            }
        }
    }
}

class InlineBlameStartup : StartupActivity {
    override fun runActivity(project: Project) {
        project.getService(InlineBlameTrigger::class.java)
    }
}

@Service(Service.Level.PROJECT)
class InlineBlameTrigger(private val project: Project) {
    init {
        val events = EditorFactory.getInstance().eventMulticaster
        val selectLine = selectLine@{ editor: Editor, line: Int ->
            if (editor.project != project) return@selectLine
            val file = FileDocumentManager.getInstance().getFile(editor.document) ?: return@selectLine
            if (!file.isInLocalFileSystem) return@selectLine
            project.getService(InlineBlameService::class.java).blameFor(file, line) { blame ->
                installInlay(editor, line, blame)
            }
        }
        events.addCaretListener(object : CaretListener {
            override fun caretPositionChanged(event: CaretEvent) = selectLine(event.editor, event.newPosition.line)
        }, project)
        events.addEditorMouseListener(object : EditorMouseListener {
            override fun mouseClicked(event: EditorMouseEvent) = selectLine(
                event.editor, event.editor.xyToLogicalPosition(event.mouseEvent.point).line,
            )
        }, project)
    }

    private fun installInlay(editor: Editor, line: Int, blame: LineBlame?) {
        editor.getUserData(BLAME_INLAY)?.dispose()
        if (blame == null || editor.isDisposed) return
        val offset = editor.document.getLineEndOffset(line)
        val text = blame.display()
        val renderer = object : EditorCustomElementRenderer {
            override fun calcWidthInPixels(inlay: Inlay<*>): Int =
                editor.contentComponent.getFontMetrics(BLAME_FONT).stringWidth(text)

            override fun paint(inlay: Inlay<*>, g: java.awt.Graphics, target: java.awt.Rectangle, attrs: TextAttributes) {
                g.color = JBColor.GRAY
                g.font = BLAME_FONT
                g.drawString(text, target.x, target.y + g.fontMetrics.ascent)
            }
        }
        editor.putUserData(BLAME_INLAY, editor.inlayModel.addAfterLineEndElement(offset, false, renderer))
    }
}
