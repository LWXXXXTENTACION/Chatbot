/**
 * Lightweight partial JSON extractor for create_artifact tool arguments.
 *
 * During streaming, tool call arguments arrive as incremental JSON fragments.
 * This utility extracts known fields (title, kind, language, content) from
 * an incomplete JSON string, so the UI can show correct metadata and
 * incremental content before the full JSON is parsed.
 */

export interface PartialArtifactInput {
  title?: string;
  kind?: string;
  language?: string;
  content?: string;
}

/**
 * Extract artifact fields from a possibly-incomplete JSON string.
 *
 * Strategy:
 * 1. Try full JSON.parse — if it succeeds, return directly.
 * 2. Use regex to extract short, complete string fields (title, kind, language).
 * 3. Manually walk the content value to handle large/partial strings with
 *    proper JSON escape handling.
 */
export function extractArtifactFields(raw: string): PartialArtifactInput {
  // 1. Try full parse first (covers the tool_call_end case)
  try {
    const parsed = JSON.parse(raw);
    return {
      title: parsed.title,
      kind: parsed.kind,
      language: parsed.language,
      content: parsed.content,
    };
  } catch {
    /* fall through to partial extraction */
  }

  const result: PartialArtifactInput = {};

  // 2. Extract short string fields via regex.
  //    Matches "key":"value" where value may contain JSON-escaped characters.
  //    Uses JSON.parse to handle unescaping safely.
  const shortFieldRe = /"(title|kind|language)"\s*:\s*"((?:[^"\\]|\\.)*)"/g;
  let match: RegExpExecArray | null;
  while ((match = shortFieldRe.exec(raw)) !== null) {
    const key = match[1];
    try {
      const value: string = JSON.parse(`"${match[2]}"`);
      if (key === "title") result.title = value;
      else if (key === "kind") result.kind = value;
      else if (key === "language") result.language = value;
    } catch {
      /* skip unparseable fragment */
    }
  }

  // 3. Extract the "content" field by walking the string manually.
  //    Content can be very large (e.g. full HTML pages), and regex would be
  //    both slow and fragile. We find `"content":"` then walk forward while
  //    tracking escape sequences to find the end of the JSON string value.
  const contentKeyRe = /"content"\s*:\s*"/;
  const cm = contentKeyRe.exec(raw);
  if (cm) {
    let i = cm.index + cm[0].length;
    let content = "";
    while (i < raw.length) {
      const ch = raw[i];
      if (ch === "\\") {
        i++;
        if (i >= raw.length) break; // incomplete escape at end
        const esc = raw[i];
        switch (esc) {
          case "n":
            content += "\n";
            break;
          case "t":
            content += "\t";
            break;
          case "r":
            content += "\r";
            break;
          case '"':
            content += '"';
            break;
          case "\\":
            content += "\\";
            break;
          case "/":
            content += "/";
            break;
          case "u": {
            // Unicode escape \uXXXX
            if (i + 4 < raw.length) {
              const hex = raw.substring(i + 1, i + 5);
              const cp = parseInt(hex, 16);
              if (!isNaN(cp)) content += String.fromCodePoint(cp);
              i += 4;
            } else {
              content += "\\u";
            }
            break;
          }
          default:
            // Unknown escape — keep as-is
            content += "\\" + esc;
        }
      } else if (ch === '"') {
        // Unescaped quote → end of the content string value.
        // Check if followed by `}` or end-of-input (streaming).
        break;
      } else {
        content += ch;
      }
      i++;
    }
    if (content) result.content = content;
  }

  return result;
}
