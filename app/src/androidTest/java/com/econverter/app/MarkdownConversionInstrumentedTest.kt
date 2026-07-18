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
import java.nio.charset.Charset
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

    private fun markdownFixture(): String =
        """
        # Android emulator E2E

        This text includes Unicode to exercise packaged encoding detection: שלום — café.

        | Feature | Result |
        |---|---|
        | Markdown | packaged runtime |
        | Mermaid | rendered for ebook output |

        ```mermaid
        flowchart LR
            Markdown["Hidden<NUL>control"] --> SVG
            SVG --> Ebook
        ```
        """.trimIndent().replace("<NUL>", 0.toChar().toString())

    private fun writeMarkdown(input: File, charset: Charset = Charsets.UTF_8) {
        input.writeBytes(markdownFixture().toByteArray(charset))
    }

    private fun visibleHtmlUnder(directory: File): String =
        if (!directory.isDirectory) {
            "<missing>"
        } else {
            directory.walkTopDown()
                .filter {
                    it.isFile &&
                        (it.extension.equals("html", true) ||
                            it.extension.equals("xhtml", true) ||
                            it.extension.equals("htm", true))
                }
                .joinToString(" ") { file ->
                    file.readText(Charsets.UTF_8)
                        .replace(Regex("<[^>]+>"), " ")
                        .replace(Regex("\\s+"), " ")
                        .trim()
                }
                .ifBlank { "<no-html>" }
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
                while (entries.hasMoreElements()) names += entries.nextElement().name

                val svgName = names.firstOrNull { it.endsWith(".svg", ignoreCase = true) }
                assertTrue("Converted EPUB does not contain the rendered Mermaid SVG: $names", svgName != null)

                val svg = epub.getInputStream(epub.getEntry(svgName!!))
                    .bufferedReader(Charsets.UTF_8)
                    .use { it.readText() }
                assertTrue("Rendered resource is not SVG", svg.contains("<svg"))
                assertTrue("Rendered SVG still contains an XML-invalid NUL", !svg.contains(0.toChar()))

                val documentText = names
                    .filter { it.endsWith(".html", true) || it.endsWith(".xhtml", true) }
                    .joinToString("\n") { name ->
                        epub.getInputStream(epub.getEntry(name)).bufferedReader(Charsets.UTF_8).use { it.readText() }
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
    fun convertsPackagedUtf16MarkdownWithMermaidToAzw3() {
        val workDir = File(startPython(), "markdown-azw3-e2e-${System.nanoTime()}").apply { mkdirs() }
        val input = File(workDir, "kindle-usb-e2e.md")
        val output = File(workDir, "kindle-usb-e2e.azw3")

        try {
            writeMarkdown(input, Charsets.UTF_16LE)

            val result = Python.getInstance().getModule("converter")
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
            assertTrue("Mermaid preprocessing did not run before AZW3 conversion", rewrittenMarkdown.contains("![Mermaid diagram]"))
            assertTrue("Markdown was not normalized to readable UTF-8", rewrittenMarkdown.contains("שלום"))
            assertTrue("Normalized Markdown still contains a NUL", !rewrittenMarkdown.contains(0.toChar()))
        } finally {
            workDir.deleteRecursively()
        }
    }

    @Test
    fun convertsPackagedUtf16PlainTextWithControlChars() {
        val workDir = File(startPython(), "txt-sanitizer-e2e-${System.nanoTime()}").apply { mkdirs() }
        val input = File(workDir, "plain-control.txt")
        val output = File(workDir, "plain-control.epub")
        val debugDir = File(workDir, "debug-pipeline")
        val source = "Plain text sanitizer\u0000 keeps readable text — שלום.\n\nSecond paragraph."

        try {
            input.writeBytes(source.toByteArray(Charsets.UTF_16LE))

            val result = Python.getInstance().getModule("converter").callAttr(
                "convert",
                input.absolutePath,
                output.absolutePath,
                "--debug-pipeline",
                debugDir.absolutePath,
            )
            val success = result.callAttr("__getitem__", "success").toBoolean()
            val message = result.callAttr("__getitem__", "message").toString()

            assertTrue(message, success)
            assertTrue("TXT conversion did not create an EPUB", output.isFile)
            assertTrue("TXT EPUB is unexpectedly empty", output.length() > 1024)

            val normalizedText = input.readText(Charsets.UTF_8)
            assertTrue("TXT was not normalized to UTF-8", normalizedText.contains("Plain text sanitizer"))
            assertTrue("TXT Unicode content was lost", normalizedText.contains("שלום"))
            assertTrue("Normalized TXT still contains an XML-invalid NUL", !normalizedText.contains(0.toChar()))

            ZipFile(output).use { epub ->
                val names = mutableListOf<String>()
                val entries = epub.entries()
                while (entries.hasMoreElements()) names += entries.nextElement().name
                val contentNames = names.filter {
                    it.endsWith(".html", true) || it.endsWith(".xhtml", true) || it.endsWith(".htm", true)
                }
                assertTrue("TXT EPUB has no HTML-family content entries: $names", contentNames.isNotEmpty())

                val documentText = contentNames.joinToString("\n") { name ->
                    epub.getInputStream(epub.getEntry(name)).bufferedReader(Charsets.UTF_8).use { it.readText() }
                }
                val visibleText = documentText
                    .replace(Regex("<[^>]+>"), " ")
                    .replace(Regex("\\s+"), " ")
                    .trim()
                val stages = listOf("input", "parsed", "structure", "processed")
                    .joinToString(" | ") { stage -> "$stage=${visibleHtmlUnder(File(debugDir, stage)).take(500)}" }
                assertTrue(
                    "Sanitized TXT content was lost in EPUB; entries=$contentNames; text=${visibleText.take(500)}; $stages",
                    visibleText.contains("Plain text sanitizer"),
                )
                assertTrue("TXT EPUB contains an XML-invalid NUL", !documentText.contains(0.toChar()))
            }
        } finally {
            workDir.deleteRecursively()
        }
    }
}
