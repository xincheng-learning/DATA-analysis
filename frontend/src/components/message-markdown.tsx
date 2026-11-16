import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export function MessageMarkdown({content}:{content:string}) {
  return <div className="message-content markdown-content"><Markdown skipHtml remarkPlugins={[remarkGfm]} components={{
    table:({children})=><div className="markdown-table-scroll"><table>{children}</table></div>,
    a:({href,children,title})=><a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>,
    img:({alt})=><span className="image-placeholder">图片：{alt || '未命名图片'}（未自动加载外部图片）</span>,
  }}>{content}</Markdown></div>;
}
