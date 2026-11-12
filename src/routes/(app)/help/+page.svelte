<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { WEBUI_NAME, mobile, showSidebar, user } from '$lib/stores';
	import { copyToClipboard } from '$lib/utils';

	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import SidebarIcon from '$lib/components/icons/Sidebar.svelte';
	import SearchIcon from '$lib/components/layout/Sidebar/icons/Search.svelte';
	import HelpCircle from '$lib/components/layout/Sidebar/icons/HelpCircle.svelte';
	import CopyIcon from '$lib/components/layout/Sidebar/icons/Copy.svelte';

	const i18n = getContext('i18n');

	// Content lives in static/help-center/ so it can be edited without touching code:
	// articles.json is the Vietnamese default, articles.<language>.json (e.g. articles.en-US.json) a translation.
	const CONTENT_DIR = '/help-center';
	const DEFAULT_FILE = 'articles.json';

	type Step = { title: string; text: string };
	type Article = {
		id: string;
		category: string;
		roles?: string[];
		title: string;
		desc?: string;
		minutes?: number;
		icon?: string;
		steps?: Step[];
		example?: string;
		tip?: string;
		images?: { src: string; caption?: string }[];
		// Single-image shorthand, kept for hand-written articles.
		image?: string;
		caption?: string;
	};
	type Content = {
		heading?: string;
		subheading?: string;
		searchPlaceholder?: string;
		updated?: string;
		featured?: string[];
		featuredAdmin?: string[];
		articles: Article[];
	};

	let content: Content | null = null;
	let error = '';
	let role: 'user' | 'admin' = 'user';
	let query = '';
	let selectedId = '';
	let searchInput: HTMLInputElement;
	let articleElement: HTMLElement;
	let zoomed: { src: string; caption?: string } | null = null;

	const brand = (text = '') => text.replaceAll('{{name}}', $WEBUI_NAME);
	const normalize = (text = '') =>
		text.toLocaleLowerCase('vi').normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd');

	$: isAdmin = $user?.role === 'admin';
	$: articles = (content?.articles ?? []).filter((article) =>
		(article.roles ?? ['user', 'admin']).includes(role)
	);
	$: results = query.trim()
		? articles.filter((article) =>
				normalize(
					[
						article.title,
						article.desc,
						article.category,
						article.example,
						article.tip,
						...(article.steps ?? []).flatMap((step) => [step.title, step.text])
					].join(' ')
				).includes(normalize(query.trim()))
			)
		: articles;
	$: categories = [...new Set(results.map((article) => article.category))];
	$: featured = query.trim()
		? results.slice(0, 3)
		: ((role === 'admin' ? content?.featuredAdmin : content?.featured) ?? [])
				.map((id) => articles.find((article) => article.id === id))
				.filter(Boolean);
	$: if (results.length && !results.some((article) => article.id === selectedId)) {
		selectedId = results[0].id;
	}
	$: selected = results.find((article) => article.id === selectedId);
	$: selectedImages = selected
		? [
				...(selected.images ?? []),
				...(selected.image ? [{ src: selected.image, caption: selected.caption }] : [])
			]
		: [];
	$: nextArticle =
		selected && results.length > 1
			? results[(results.findIndex((article) => article.id === selected.id) + 1) % results.length]
			: null;

	const open = (id: string) => {
		selectedId = id;
		if ($mobile) articleElement?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	};

	const switchRole = (value: 'user' | 'admin') => {
		role = value;
		query = '';
		selectedId = '';
	};

	const copyExample = async (text: string) => {
		if (await copyToClipboard(text)) {
			toast.success($i18n.t('Copying to clipboard was successful!'));
		}
	};

	const onKeydown = (event: KeyboardEvent) => {
		if (event.key === 'Escape' && zoomed) {
			zoomed = null;
			return;
		}
		if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
			event.preventDefault();
			searchInput?.focus();
		}
	};

	const fetchContent = async (file: string): Promise<Content> => {
		const res = await fetch(`${CONTENT_DIR}/${file}`, { cache: 'no-store' });
		if (!res.ok) throw new Error(`${file}: HTTP ${res.status}`);
		const text = await res.text();
		try {
			return JSON.parse(text);
		} catch (e) {
			throw new Error(`${file} không hợp lệ: ${e.message}`);
		}
	};

	let loadedLanguage = '';
	const load = async (language: string) => {
		loadedLanguage = language;
		error = '';
		let loaded: Content | null = null;
		// A missing translation can come back as the app's HTML page, so any failure falls back to the default file.
		if (language && !language.startsWith('vi')) {
			loaded = await fetchContent(`articles.${language}.json`).catch(() => null);
		}
		try {
			loaded = loaded ?? (await fetchContent(DEFAULT_FILE));
		} catch (e) {
			error = `${e.message ?? e}`;
			return;
		}
		if (language !== loadedLanguage) return; // the language changed while loading
		content = loaded;
		selectedId = (content?.featured ?? [])[0] ?? content?.articles?.[0]?.id ?? '';
	};

	$: if (($i18n.language ?? '') !== loadedLanguage) load($i18n.language ?? '');
</script>

<svelte:window on:keydown={onKeydown} />

<svelte:head>
	<title>
		{$i18n.t('User Guide')} / {$WEBUI_NAME}
	</title>
</svelte:head>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<nav class="px-1.5 pt-1.5 pb-2 md:px-3 md:pt-2 drag-region select-none shrink-0">
		<div class="flex items-center gap-1">
			{#if $mobile}
				<div class="{$showSidebar ? 'md:hidden' : ''} flex flex-none items-center self-start">
					<Tooltip content={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}>
						<button
							class="cursor-pointer flex rounded-lg hover:bg-gray-100 dark:hover:bg-gray-850 transition"
							aria-label={$showSidebar ? $i18n.t('Close Sidebar') : $i18n.t('Open Sidebar')}
							on:click={() => showSidebar.set(!$showSidebar)}
						>
							<div class="self-center p-1.5">
								<SidebarIcon className="size-4" />
							</div>
						</button>
					</Tooltip>
				</div>
			{/if}
			<div class="flex w-full items-center justify-between py-1 px-1">
				<div class="text-sm text-gray-500 dark:text-gray-400 truncate">
					{$WEBUI_NAME} /
					<span class="text-gray-900 dark:text-gray-100">{$i18n.t('User Guide')}</span>
				</div>
				{#if isAdmin}
					<div
						class="flex gap-0.5 rounded-xl p-0.5 bg-gray-50 dark:bg-gray-850 border border-gray-100 dark:border-gray-800 text-xs"
						role="group"
						aria-label={$i18n.t('View as')}
					>
						{#each [['user', $i18n.t('User')], ['admin', $i18n.t('Admin')]] as [value, label]}
							<button
								type="button"
								class="px-2.5 py-1 rounded-lg transition {role === value
									? 'bg-white dark:bg-gray-800 text-gray-900 dark:text-white shadow-sm font-medium'
									: 'text-gray-500 hover:text-gray-800 dark:hover:text-gray-200'}"
								aria-pressed={role === value}
								on:click={() => switchRole(value)}
							>
								{label}
							</button>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto">
		<div class="max-w-6xl mx-auto px-4 md:px-6 pb-16">
			{#if error}
				<div
					class="mt-6 rounded-2xl border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 p-5 text-sm text-red-700 dark:text-red-300"
				>
					<div class="font-medium mb-1">{$i18n.t('Could not load the user guide')}</div>
					<div class="text-xs opacity-80">{error}</div>
				</div>
			{:else if !content}
				<div class="flex justify-center py-20"><Spinner className="size-5" /></div>
			{:else}
				<section
					class="mt-2 rounded-3xl border border-gray-100 dark:border-gray-850 bg-gray-50 dark:bg-gray-900 px-5 py-7 md:px-8 md:py-9"
				>
					<div
						class="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-gray-500 dark:text-gray-400"
					>
						<HelpCircle className="size-3.5" />
						{$i18n.t('Help Center')}
					</div>
					<h1 class="mt-2 text-2xl md:text-3xl font-semibold text-gray-900 dark:text-white">
						{brand(content.heading)}
					</h1>
					{#if content.subheading}
						<p class="mt-1.5 text-sm text-gray-500 dark:text-gray-400 max-w-2xl">
							{brand(content.subheading)}
						</p>
					{/if}
					<label
						class="mt-5 flex items-center gap-2 max-w-2xl rounded-2xl bg-white dark:bg-gray-850 border border-gray-200 dark:border-gray-800 px-3.5 focus-within:border-gray-400 dark:focus-within:border-gray-600 transition"
					>
						<SearchIcon className="size-4 text-gray-400" strokeWidth="1.5" />
						<input
							bind:this={searchInput}
							bind:value={query}
							class="flex-1 min-w-0 bg-transparent outline-none py-3 text-sm placeholder:text-gray-400"
							placeholder={brand(content.searchPlaceholder) || $i18n.t('Search')}
							aria-label={$i18n.t('Search')}
							autocomplete="off"
						/>
						<span
							class="hidden sm:inline text-[0.6875rem] text-gray-400 border border-gray-200 dark:border-gray-700 rounded-md px-1.5 py-0.5"
							>Ctrl K</span
						>
					</label>
				</section>

				<div class="mt-7 mb-3 text-sm font-medium text-gray-900 dark:text-gray-100">
					{query.trim()
						? `${$i18n.t('Search results')} · ${results.length}`
						: $i18n.t('Featured topics')}
				</div>
				{#if featured.length}
					<div class="grid grid-cols-1 md:grid-cols-3 gap-3">
						{#each featured as article (article.id)}
							<button
								type="button"
								class="flex items-center gap-3 text-left rounded-2xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900 px-4 py-3.5 hover:bg-gray-50 dark:hover:bg-gray-850 transition min-w-0"
								on:click={() => open(article.id)}
							>
								<span
									class="flex size-10 shrink-0 items-center justify-center rounded-xl bg-gray-100 dark:bg-gray-800 text-lg"
									aria-hidden="true">{article.icon ?? '📘'}</span
								>
								<span class="min-w-0">
									<span class="block text-sm font-medium text-gray-900 dark:text-gray-100 truncate"
										>{brand(article.title)}</span
									>
									<span class="block text-xs text-gray-500 dark:text-gray-400 truncate"
										>{brand(article.desc)}</span
									>
								</span>
							</button>
						{/each}
					</div>
				{:else}
					<div class="text-sm text-gray-500">{$i18n.t('No matching topics.')}</div>
				{/if}

				<div class="mt-6 grid grid-cols-1 md:grid-cols-[240px_minmax(0,1fr)] gap-5 items-start">
					<nav
						class="md:sticky md:top-0 rounded-2xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900 p-2 max-h-72 md:max-h-none overflow-y-auto"
						aria-label={$i18n.t('Guide contents')}
					>
						{#each categories as category}
							<div
								class="px-2.5 pt-3 pb-1 text-[0.6875rem] font-medium uppercase tracking-wider text-gray-400 dark:text-gray-500"
							>
								{category}
							</div>
							{#each results.filter((article) => article.category === category) as article (article.id)}
								<button
									type="button"
									class="w-full flex items-center gap-2 text-left rounded-xl px-2.5 py-1.5 text-[0.8125rem] transition {selectedId ===
									article.id
										? 'bg-black/[0.045] dark:bg-white/[0.06] text-gray-900 dark:text-white font-medium'
										: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-850'}"
									aria-current={selectedId === article.id ? 'page' : undefined}
									on:click={() => open(article.id)}
								>
									<span class="w-4 text-center text-xs" aria-hidden="true"
										>{(article.roles ?? []).length === 1 ? '◆' : '·'}</span
									>
									<span class="truncate">{brand(article.title)}</span>
								</button>
							{/each}
						{:else}
							<div class="px-3 py-6 text-xs text-gray-500">
								{$i18n.t('No matching topics. Try another keyword.')}
							</div>
						{/each}
					</nav>

					<article
						bind:this={articleElement}
						class="rounded-2xl border border-gray-100 dark:border-gray-850 bg-white dark:bg-gray-900 px-5 py-6 md:px-8 md:py-7 min-h-[28rem]"
						aria-live="polite"
					>
						{#if selected}
							<div class="flex items-center justify-between gap-3 text-xs text-gray-500">
								<span
									class="rounded-full bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 px-2.5 py-1 font-medium"
									>{selected.category}</span
								>
								{#if selected.minutes}
									<span>{$i18n.t('{{COUNT}} min read', { COUNT: selected.minutes })}</span>
								{/if}
							</div>
							<h2 class="mt-3 text-xl md:text-2xl font-semibold text-gray-900 dark:text-white">
								{brand(selected.title)}
							</h2>
							{#if selected.desc}
								<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">{brand(selected.desc)}</p>
							{/if}

							{#if selected.steps?.length}
								<h3 class="mt-6 mb-3 text-sm font-medium text-gray-900 dark:text-gray-100">
									{$i18n.t('Steps')}
								</h3>
								<ol class="flex flex-col gap-3.5">
									{#each selected.steps as step, index}
										<li class="flex gap-3">
											<span
												class="flex size-6 shrink-0 items-center justify-center rounded-full bg-gray-900 dark:bg-white text-white dark:text-gray-900 text-xs font-medium"
												>{index + 1}</span
											>
											<div class="min-w-0">
												<div class="text-sm font-medium text-gray-900 dark:text-gray-100">
													{brand(step.title)}
												</div>
												<div class="text-sm text-gray-600 dark:text-gray-400 leading-relaxed">
													{brand(step.text)}
												</div>
											</div>
										</li>
									{/each}
								</ol>
							{/if}

							{#if selected.example}
								<h3 class="mt-6 mb-2 text-sm font-medium text-gray-900 dark:text-gray-100">
									{$i18n.t('Example prompt')}
								</h3>
								<div
									class="flex items-start gap-2 rounded-xl bg-gray-50 dark:bg-gray-850 border border-gray-100 dark:border-gray-800 px-3.5 py-3"
								>
									<code class="flex-1 text-[0.8125rem] text-gray-800 dark:text-gray-200 break-words"
										>{brand(selected.example)}</code
									>
									<Tooltip content={$i18n.t('Copy')}>
										<button
											type="button"
											class="p-1 rounded-lg text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 transition"
											aria-label={$i18n.t('Copy')}
											on:click={() => copyExample(brand(selected.example))}
										>
											<CopyIcon className="size-4" />
										</button>
									</Tooltip>
								</div>
							{/if}

							{#if selected.tip}
								<div
									class="mt-6 flex gap-2.5 rounded-xl border-l-2 border-gray-400 dark:border-gray-600 bg-gray-50 dark:bg-gray-850 px-3.5 py-3 text-[0.8125rem] text-gray-600 dark:text-gray-300"
								>
									<HelpCircle className="size-4 shrink-0 mt-0.5" />
									<span
										><span class="font-medium text-gray-900 dark:text-gray-100"
											>{$i18n.t('Guide tip')}:</span
										>
										{brand(selected.tip)}</span
									>
								</div>
							{/if}

							{#each selectedImages as image (image.src)}
								<figure
									class="mt-6 rounded-xl border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 p-2"
								>
									<button
										type="button"
										class="block w-full cursor-zoom-in"
										aria-label={$i18n.t('View larger image')}
										on:click={() => (zoomed = image)}
									>
										<img
											class="w-full max-h-[22rem] object-contain rounded-lg"
											src={image.src}
											alt={brand(image.caption ?? selected.title)}
											loading="lazy"
										/>
									</button>
									{#if image.caption}
										<figcaption class="pt-2 text-center text-xs text-gray-500">
											{brand(image.caption)}
										</figcaption>
									{/if}
								</figure>
							{/each}
								</figure>
							{/if}

							<div
								class="mt-8 pt-4 border-t border-gray-100 dark:border-gray-850 flex items-center justify-between text-xs text-gray-500"
							>
								<span
									>{content.updated ? `${$i18n.t('Last updated')}: ${content.updated}` : ''}</span
								>
								{#if nextArticle}
									<button
										type="button"
										class="font-medium text-gray-900 dark:text-gray-100 hover:underline"
										on:click={() => open(nextArticle.id)}
									>
										{$i18n.t('Next article')} →
									</button>
								{/if}
							</div>
						{:else}
							<div class="py-16 text-center text-sm text-gray-500">
								{$i18n.t('No matching topics. Try another keyword.')}
							</div>
						{/if}
					</article>
				</div>
			{/if}
		</div>
	</div>
</div>

{#if zoomed}
	<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
	<div
		class="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/80 p-4 cursor-zoom-out"
		on:click={() => (zoomed = null)}
	>
		<img
			class="max-w-full max-h-[85vh] object-contain rounded-lg"
			src={zoomed.src}
			alt={brand(zoomed.caption ?? '')}
		/>
		{#if zoomed.caption}
			<div class="mt-3 text-sm text-gray-200">{brand(zoomed.caption)}</div>
		{/if}
	</div>
{/if}
