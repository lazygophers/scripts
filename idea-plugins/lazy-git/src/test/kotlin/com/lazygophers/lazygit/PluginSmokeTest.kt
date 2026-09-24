package com.lazygophers.lazygit

import javax.xml.parsers.DocumentBuilderFactory
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class PluginSmokeTest {
    @Test
    fun `plugin metadata declares identity and Git dependencies`() {
        val document = requireNotNull(javaClass.getResourceAsStream("/META-INF/plugin.xml")).use {
            DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(it)
        }
        val root = document.documentElement
        val dependencies = root.getElementsByTagName("depends")
            .let { nodes -> (0 until nodes.length).map { nodes.item(it).textContent.trim() } }

        assertEquals("com.lazygophers.lazygit", root.getElementsByTagName("id").item(0).textContent)
        assertTrue(root.getElementsByTagName("description").item(0).textContent.contains("中文界面"))
        assertTrue("com.intellij.modules.vcs" in dependencies)
        assertTrue("Git4Idea" in dependencies)
    }
}
