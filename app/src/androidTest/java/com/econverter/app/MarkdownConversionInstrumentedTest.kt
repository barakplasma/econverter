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
    private fun startPython(): File {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(context))
        }
        return context.cacheDir
    }

    private fun writeMarkdown(input: File) {
        input.writeText(
            """
            # Android emulator E2E

            This text includes Unicode to exercise packaged encoding detection: שלום — café.

            | Feature | Result |
            |---|---|
            | Markdown | packaged runtime |
            | Mermaid | rendered for ebook output |

            ```mermaid
            flowchart LR
                Markdown --> SVG
                SVG --> Ebook
            ```
            """.trimIndent(),
            Charsets.UTF_8,
        )
    }

    @Test
    fun convertsPackagedMarkdownWithMermaidToEpub3() {
        val workDir = File(startPython(), "markdown-epub3-e2e-${System.nanoTime()}").apply { mkdirs() }
        val input = File(workDir, "android-e2e.md")
        val output = File(workDir, "android-e2e.epub")

        try {
            writeMarkdown(input)

            val result = Python.getInstance()
                .getModule("converter")
                .callAttr(
                    "convert",
                    input.absolutePath,
                    output.absolutePath,
                    "--epub-version",
                    "3",
                )
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

    @Test
    fun convertsPackagedMarkdownWithMermaidToAzw3() {
        val workDir = File(startPython(), "markdown-azw3-e2e-${System.nanoTime()}").apply { mkdirs() }
        val input = File(workDir, "kindle-usb-e2e.md")
        val output = File(workDir, "kindle-usb-e2e.azw3")

        try {
            writeMarkdown(input)

            val result = Python.getInstance()
                .getModule("converter")
                .callAttr("convert", input.absolutePath, output.absolutePath)
            val success = result.callAttr("__getitem__", "success").toBoolean()
            val message = result.callAttr("__getitem__", "message").toString()

            assertTrue(message, success)
            assertTrue("AZW3 was not created", output.isFile)
            assertTrue("AZW3 is unexpectedly empty", output.length() > 4096)
            assertTrue("Output does not have the Kindle .azw3 extension", output.name.endsWith(".azw3"))

            val header = ByteArray(68)
            val bytesRead = output.inputStream().use { it.read(header) }
            assertEquals("AZW3 header is truncated", header.size, bytesRead)
            assertEquals(
                "AZW3 does not have the expected PalmDB/Kindle BOOKMOBI signature",
                "BOOKMOBI",
                header.copyOfRange(60, 68).toString(Charsets.US_ASCII),
            )

            val rewrittenMarkdown = input.readText(Charsets.UTF_8)
            assertTrue(
                "Mermaid preprocessing did not run before AZW3 conversion",
                rewrittenMarkdown.contains("![Mermaid diagram]"),
            )
        } finally {
            workDir.deleteRecursively()
        }
    }
}
