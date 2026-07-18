package com.econverter.app

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.zip.ZipFile

@RunWith(AndroidJUnit4::class)
class MarkdownConversionInstrumentedTest {
    @Test
    fun convertsPackagedMarkdownWithMermaidToEpub() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }

        val workDir = File(context.cacheDir, "markdown-e2e-${System.nanoTime()}").apply { mkdirs() }
        val input = File(workDir, "android-e2e.md")
        val output = File(workDir, "android-e2e.epub")

        try {
            input.writeText(
                """
                # Android emulator E2E

                This text includes Unicode to exercise packaged encoding detection: שלום — café.

                | Feature | Result |
                |---|---|
                | Markdown | packaged runtime |
                | Mermaid | SVG in EPUB |

                ```mermaid
                flowchart LR
                    Markdown --> SVG
                    SVG --> EPUB
                ```
                """.trimIndent(),
                Charsets.UTF_8,
            )

            val result = Python.getInstance()
                .getModule("converter")
                .callAttr("convert", input.absolutePath, output.absolutePath)
            val success = result.callAttr("__getitem__", "success").toBoolean()
            val message = result.callAttr("__getitem__", "message").toString()

            assertTrue(message, success)
            assertTrue("EPUB was not created", output.isFile)
            assertTrue("EPUB is unexpectedly empty", output.length() > 1024)

            ZipFile(output).use { epub ->
                val mimetype = epub.getInputStream(epub.getEntry("mimetype"))
                    .bufferedReader(Charsets.UTF_8)
                    .use { it.readText().trim() }
                assertEquals("application/epub+zip", mimetype)

                val names = mutableListOf<String>()
                val entries = epub.entries()
                while (entries.hasMoreElements()) {
                    names += entries.nextElement().name
                }

                val svgName = names.firstOrNull { it.endsWith(".svg", ignoreCase = true) }
                assertTrue("Converted EPUB does not contain the rendered Mermaid SVG: $names", svgName != null)

                val svg = epub.getInputStream(epub.getEntry(svgName!!))
                    .bufferedReader(Charsets.UTF_8)
                    .use { it.readText() }
                assertTrue("Rendered resource is not SVG", svg.contains("<svg"))

                val documentText = names
                    .filter { it.endsWith(".html", true) || it.endsWith(".xhtml", true) }
                    .joinToString("\n") { name ->
                        epub.getInputStream(epub.getEntry(name))
                            .bufferedReader(Charsets.UTF_8)
                            .use { it.readText() }
                    }
                assertTrue("Heading was lost during conversion", documentText.contains("Android emulator E2E"))
                assertTrue("Unicode content was lost during conversion", documentText.contains("שלום"))
                assertTrue("Mermaid image reference was not retained", documentText.contains(".svg"))
            }
        } finally {
            workDir.deleteRecursively()
        }
    }
}
