import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const outputDirectory = path.join(scriptDirectory, "data", "annual_reports", "2025");

const companies = [
  { code: "601398", name: "工商银行", fullName: "中国工商银行股份有限公司", column: "sse", plate: "sh" },
  { code: "601939", name: "建设银行", fullName: "中国建设银行股份有限公司", column: "sse", plate: "sh" },
  { code: "601288", name: "农业银行", fullName: "中国农业银行股份有限公司", column: "sse", plate: "sh" },
  { code: "601988", name: "中国银行", fullName: "中国银行股份有限公司", column: "sse", plate: "sh" },
  { code: "601328", name: "交通银行", fullName: "交通银行股份有限公司", column: "sse", plate: "sh" },
  { code: "600036", name: "招商银行", fullName: "招商银行股份有限公司", column: "sse", plate: "sh" },
  { code: "601166", name: "兴业银行", fullName: "兴业银行股份有限公司", column: "sse", plate: "sh" },
  { code: "600000", name: "浦发银行", fullName: "上海浦东发展银行股份有限公司", column: "sse", plate: "sh" },
  { code: "601998", name: "中信银行", fullName: "中信银行股份有限公司", column: "sse", plate: "sh" },
  { code: "000001", name: "平安银行", fullName: "平安银行股份有限公司", column: "szse", plate: "sz" },
];

const queryUrl = "https://www.cninfo.com.cn/new/hisAnnouncement/query";
const headers = {
  "user-agent": "Mozilla/5.0",
  referer: "https://www.cninfo.com.cn/",
};

function plainTitle(title) {
  return title.replace(/<[^>]+>/g, "").replace(/\s/g, "");
}

function csvCell(value) {
  return `"${String(value).replaceAll('"', '""')}"`;
}

function shanghaiDate(milliseconds) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  })
    .formatToParts(new Date(milliseconds))
    .reduce((result, part) => ({ ...result, [part.type]: part.value }), {});
  return `${parts.year}-${parts.month}-${parts.day}`;
}

await mkdir(outputDirectory, { recursive: true });
const records = [];

for (const company of companies) {
  const body = new URLSearchParams({
    pageNum: "1",
    pageSize: "30",
    column: company.column,
    tabName: "fulltext",
    plate: company.plate,
    searchkey: company.name,
    category: "category_ndbg_szsh;",
    seDate: "2026-01-01~2026-09-29",
    isHLtitle: "true",
  });

  const queryResponse = await fetch(queryUrl, {
    method: "POST",
    headers: { ...headers, "content-type": "application/x-www-form-urlencoded; charset=UTF-8" },
    body,
  });
  if (!queryResponse.ok) throw new Error(`${company.name} 公告查询失败：HTTP ${queryResponse.status}`);
  const data = await queryResponse.json();
  const candidates = (data.announcements ?? []).filter((announcement) => {
    const title = plainTitle(announcement.announcementTitle);
    return (
      announcement.secCode === company.code &&
      /2025.*年度报告/.test(title) &&
      !/(摘要|H股|英文|股东会)/.test(title)
    );
  });
  if (candidates.length === 0) {
    throw new Error(`未找到 ${company.name}（${company.code}）的2025年年度报告全文。`);
  }

  candidates.sort(
    (a, b) => b.announcementTime - a.announcementTime || Number(b.adjunctSize) - Number(a.adjunctSize),
  );
  const selected = candidates[0];
  const title = selected.announcementTitle.replace(/<[^>]+>/g, "");
  const pdfUrl = `https://static.cninfo.com.cn/${selected.adjunctUrl}`;
  const fileName = `${company.code}_${company.name}_2025年年度报告.pdf`;
  const filePath = path.join(outputDirectory, fileName);

  const pdfResponse = await fetch(pdfUrl, { headers });
  if (!pdfResponse.ok) throw new Error(`${fileName} 下载失败：HTTP ${pdfResponse.status}`);
  const bytes = Buffer.from(await pdfResponse.arrayBuffer());
  if (bytes.length < 100_000 || bytes.subarray(0, 4).toString("ascii") !== "%PDF") {
    throw new Error(`${fileName} 下载结果不是有效的年度报告PDF。`);
  }
  await writeFile(filePath, bytes);

  const date = shanghaiDate(selected.announcementTime);
  const detailUrl = new URL("https://www.cninfo.com.cn/new/disclosure/detail");
  detailUrl.search = new URLSearchParams({
    stockCode: company.code,
    announcementId: selected.announcementId,
    orgId: selected.orgId,
    announcementTime: date,
  });
  records.push({
    code: company.code,
    bank: company.name,
    report_title: title,
    report_year: 2025,
    disclosure_date: date,
    source: "巨潮资讯网",
    disclosure_page: detailUrl.toString(),
    pdf_url: pdfUrl,
    local_file: fileName,
    file_size_bytes: bytes.length,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  });
  console.log(`已下载：${fileName}`);
}

const columns = Object.keys(records[0]);
const csv = [columns.map(csvCell).join(","), ...records.map((record) => columns.map((key) => csvCell(record[key])).join(","))].join("\n");
await writeFile(path.join(outputDirectory, "sources.csv"), `\ufeff${csv}\n`, "utf8");

const markdown = [
  "# 2025年年度报告来源清单",
  "",
  "以下报告均从巨潮资讯网的上市公司公告系统下载，均为年度报告全文。",
  "",
  "| 证券代码 | 银行 | 报告标题 | 披露日期 | 公告页面 | PDF | 本地文件 |",
  "|---|---|---|---|---|---|---|",
  ...records.map(
    (record) =>
      `| ${record.code} | ${record.bank} | ${record.report_title} | ${record.disclosure_date} | [查看公告](${record.disclosure_page}) | [下载PDF](${record.pdf_url}) | ${record.local_file} |`,
  ),
  "",
  "来源说明：巨潮资讯网是深圳证券交易所法定信息披露平台。本清单同时保存直接PDF链接、公告详情页、文件大小及SHA-256校验值，便于复核与复现。",
  "",
];
await writeFile(path.join(outputDirectory, "sources.md"), markdown.join("\n"), "utf8");
console.log(`完成：共下载 ${records.length} 份报告。`);
