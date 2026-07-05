const cheerio = require('cheerio');
const superagent = require('superagent');
const legacy = require('./detail.legacy');
const { log } = require('../utils/utils');

const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36';

function cleanText(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
}

function extractSubjectId(url) {
    const match = String(url || '').match(/\/subject\/(\d+)\/?/);
    return match ? match[1] : '';
}

function formatTitle(title, year) {
    title = cleanText(title);
    year = cleanText(year);
    if (!title) {
        return '';
    }
    if (year && !new RegExp(`\\b${year}\\b`).test(title)) {
        return `${title}(${year})`;
    }
    return title;
}

async function requestText(url, headers = {}) {
    const response = await superagent
        .get(url)
        .set({
            'User-Agent': USER_AGENT,
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            ...headers,
        })
        .timeout({ response: 8000, deadline: 12000 });
    return response.text || '';
}

async function requestJson(url, headers = {}) {
    const response = await superagent
        .get(url)
        .set({
            'User-Agent': USER_AGENT,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            ...headers,
        })
        .timeout({ response: 8000, deadline: 12000 });
    if (response.body && Object.keys(response.body).length) {
        return response.body;
    }
    return JSON.parse(response.text || '{}');
}

async function fromSubjectAbstract(subjectId) {
    const data = await requestJson(`https://movie.douban.com/j/subject_abstract?subject_id=${subjectId}`, {
        Referer: `https://movie.douban.com/subject/${subjectId}/`,
    });
    const subject = data.subject || data;
    const title = formatTitle(subject.title || subject.name, subject.release_year || subject.year);
    if (!title) {
        return null;
    }
    return {
        title,
        rating: cleanText(subject.rate || subject.rating),
        content_intro: cleanText(subject.short_comment && subject.short_comment.content),
        acting_staff: subject.actors || [],
        imgs: [],
    };
}

async function fromRexxar(subjectId) {
    const data = await requestJson(`https://m.douban.com/rexxar/api/v2/movie/${subjectId}`, {
        Referer: `https://m.douban.com/movie/subject/${subjectId}/`,
    });
    const title = formatTitle(data.title || data.name, data.year || data.release_year);
    if (!title) {
        return null;
    }
    return {
        title,
        pic: data.pic && data.pic.normal,
        rating: cleanText(data.rating && (data.rating.value || data.rating.count)),
        content_intro: cleanText(data.intro),
        acting_staff: [],
        imgs: [],
    };
}

async function fromMobileHtml(subjectId) {
    const html = await requestText(`https://m.douban.com/movie/subject/${subjectId}/`, {
        Referer: 'https://m.douban.com/movie/',
    });
    const $ = cheerio.load(html);
    let title = $('meta[property="og:title"]').attr('content') || $('title').text();
    title = cleanText(title)
        .replace(/\s*[-|]\s*豆瓣.*$/i, '')
        .replace(/\s*\(豆瓣\)\s*$/i, '');
    if (!title || title === '豆瓣') {
        return null;
    }
    return {
        title,
        rating: cleanText($('.rating .rating-stars').attr('data-rating') || $('[property="v:average"]').text()),
        content_intro: cleanText($('[property="v:summary"]').text()),
        acting_staff: [],
        imgs: [],
    };
}

const Detail = {
    async detail(req, res) {
        const startTime = Date.now();
        const url = req.query.url;
        if (!url) {
            res.json({ status: false, msg: '参数有误', data: null });
            return false;
        }

        const data = await Detail._detail(url, '1002');
        const endTime = Date.now();
        res.json({
            status: true,
            msg: '获取成功',
            time: (endTime - startTime) / 1000 + 's',
            data,
        });
    },

    async _detail(url, catNum) {
        if (catNum !== '1002') {
            return legacy._detail(url, catNum);
        }
        return Detail._detailHandle_1002(url);
    },

    async _detailHandle_1002(url) {
        const subjectId = extractSubjectId(url);
        if (subjectId) {
            for (const loader of [fromSubjectAbstract, fromRexxar, fromMobileHtml]) {
                try {
                    const data = await loader(subjectId);
                    if (data && data.title) {
                        return data;
                    }
                } catch (e) {
                    log(`电影详情 fallback 失败：${e.message || e}`);
                }
            }
        }

        const data = await legacy._detail(url, '1002');
        return data || { title: '', rating: '', content_intro: '', acting_staff: [], imgs: [] };
    },

    infoHandle: legacy.infoHandle,
};

module.exports = Detail;
