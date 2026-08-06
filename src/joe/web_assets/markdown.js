(function () {
  "use strict";

  function escapeHtml(value) {
    const node = document.createElement("span");
    node.textContent = value;
    return node.innerHTML;
  }

  function renderMarkdown(target, source) {
    target.dataset.source = String(source || "");
    const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) {
        index += 1;
        continue;
      }
      if (line.trim().startsWith("```")) {
        const language = line.trim().slice(3).trim();
        const code = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith("```")) {
          code.push(lines[index]);
          index += 1;
        }
        index += index < lines.length ? 1 : 0;
        html.push(`<pre><code${language ? ` data-language="${escapeHtml(language)}"` : ""}>${escapeHtml(code.join("\n"))}</code></pre>`);
        continue;
      }
      const heading = line.match(/^(#{1,4})\s+(.+)$/);
      if (heading) {
        const level = heading[1].length;
        html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }
      if (index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
        const headers = tableCells(line);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(tableCells(lines[index]));
          index += 1;
        }
        html.push(`<div class="table-scroll"><table><thead><tr>${headers.map(cell => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_, column) => `<td>${inlineMarkdown(row[column] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
        continue;
      }
      if (/^\s*[-*+]\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
          items.push(lines[index].replace(/^\s*[-*+]\s+/, ""));
          index += 1;
        }
        html.push(`<ul>${items.map(item => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
        continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
          items.push(lines[index].replace(/^\s*\d+\.\s+/, ""));
          index += 1;
        }
        html.push(`<ol>${items.map(item => `<li>${inlineMarkdown(item)}</li>`).join("")}</ol>`);
        continue;
      }
      if (/^>\s?/.test(line)) {
        const quotes = [];
        while (index < lines.length && /^>\s?/.test(lines[index])) {
          quotes.push(lines[index].replace(/^>\s?/, ""));
          index += 1;
        }
        html.push(`<blockquote>${inlineMarkdown(quotes.join(" "))}</blockquote>`);
        continue;
      }
      if (/^---+$/.test(line.trim())) {
        html.push("<hr>");
        index += 1;
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines, index)) {
        paragraph.push(lines[index]);
        index += 1;
      }
      html.push(`<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`);
    }
    target.innerHTML = html.join("");
    target.classList.add("markdown");
  }

  function startsMarkdownBlock(lines, index) {
    const line = lines[index];
    return /^(#{1,4})\s+/.test(line)
      || line.trim().startsWith("```")
      || /^\s*[-*+]\s+/.test(line)
      || /^\s*\d+\.\s+/.test(line)
      || /^>\s?/.test(line)
      || /^---+$/.test(line.trim())
      || (index + 1 < lines.length && isTableSeparator(lines[index + 1]));
  }

  function isTableSeparator(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  }

  function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
  }

  function inlineMarkdown(value) {
    const code = [];
    let text = escapeHtml(value).replace(/`([^`]+)`/g, (_, content) => {
      code.push(content);
      return `\u0000CODE${code.length - 1}\u0000`;
    });
    text = text
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return text.replace(/\u0000CODE(\d+)\u0000/g, (_, position) => `<code>${code[Number(position)]}</code>`);
  }

  function extractQuestion(text) {
    // L'IA n'a aucun canal interactif : elle termine son tour sur un bloc
    // `joe:question`, que Joe transforme en boutons. Le clic renvoie l'option
    // choisie comme message suivant — ni blocage, ni protocole.
    const match = /```joe:question\s*\n([\s\S]*?)```/.exec(text || "");
    if (!match) return null;
    let parsed;
    try {
      parsed = JSON.parse(match[1]);
    } catch (error) {
      return null;
    }
    const options = Array.isArray(parsed && parsed.options)
      ? parsed.options.map(item => String(item)).filter(Boolean).slice(0, 4)
      : [];
    if (!parsed || !parsed.question || options.length < 2) return null;
    return {
      question: String(parsed.question),
      options,
      body: text.replace(match[0], "").trimEnd()
    };
  }

  // Étapes d'un plan : ce qui est écrit en liste devient une étape autonome.
  // Le plan vient de Joe (mode plan) ou de l'utilisateur ; les deux passent ici.
  function planSteps(markdown) {
    const steps = [];
    for (const line of String(markdown || "").split("\n")) {
      const item = line.match(/^\s*(?:[-*+]|\d+[.)])\s+(.*\S)/);
      if (!item) continue;
      const step = item[1].replace(/\*\*/g, "").replace(/`/g, "").trim();
      if (step) steps.push(step);
    }
    return steps;
  }

  window.JoeMarkdown = {
    renderMarkdown, isTableSeparator, extractQuestion, planSteps
  };
}());
