/** 마크다운을 브라우저의 다운로드 폴더에 파일로 저장한다. */
export function downloadMarkdown(markdown, filename) {
  const url = URL.createObjectURL(new Blob([`${markdown}\n`], { type: "text/markdown;charset=utf-8" }));
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}
