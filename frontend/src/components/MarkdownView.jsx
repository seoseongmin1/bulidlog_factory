import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const components = {
  // 매뉴얼 출처 링크(#manual:문서번호)는 같은 화면의 매뉴얼 탭으로 옮긴다. 다른 링크는 새 창으로 연다.
  a: ({ href = "", children, node: _node, ...props }) =>
    href.startsWith("#manual:") ? (
      <a className="cite" href={href} {...props}>
        {children}
      </a>
    ) : (
      <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
        {children}
      </a>
    ),
  table: (props) => (
    <div className="table-wrap">
      <table {...props} />
    </div>
  ),
};

/** 마크다운을 화면에 보여 준다. HTML은 해석하지 않으므로 AI 답변에 섞인 태그는 실행되지 않는다. */
export default function MarkdownView({ text }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
