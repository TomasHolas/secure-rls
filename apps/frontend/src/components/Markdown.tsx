/**
 * Markdown brick: render a markdown string as sanitized GFM HTML, ported from the KB
 * (`knowledgebase/apps/frontend/src/components/Markdown.tsx`) without its record-domain
 * extras. `rehypeSanitize` is what makes model-written markup safe to render: raw HTML in
 * the string is stripped, so an answer cannot smuggle a tag past the renderer.
 */

import ReactMarkdown, { type Components } from "react-markdown";
import type { ReactNode } from "react";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";

const COMPONENTS: Components = {
  a({ href, children }) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
};

export function Markdown({ children }: { children: string }): ReactNode {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeSanitize]}
      components={COMPONENTS}
    >
      {children}
    </ReactMarkdown>
  );
}
