export async function ensureTabData(context) {
    if (!context.moduleVisitState.subscription) {
        await context.refreshSubscriptionState();
        context.moduleVisitState.subscription = true;
    }
}

function mergeSubscriptionTaskUpdates(tasks = [], updates = []) {
    const sourceTasks = Array.isArray(tasks) ? tasks : [];
    const updateMap = new Map();
    (Array.isArray(updates) ? updates : []).forEach((item) => {
        const name = String(item?.name || item?.task_name || '').trim();
        if (!name) return;
        updateMap.set(name, item);
    });
    if (!updateMap.size) return sourceTasks;
    return sourceTasks.map((task) => {
        const name = String(task?.name || task?.task_name || '').trim();
        if (!name || !updateMap.has(name)) return task;
        const update = updateMap.get(name) || {};
        return {
            ...task,
            ...update,
            name: task?.name || update.name || name,
        };
    });
}

export function applySubscriptionState(data, {
    forceRender = false,
    getSubscriptionState,
    setSubscriptionState,
    getIntroExpanded,
    setIntroExpanded,
    pruneTaskIntroExpanded,
    buildSubscriptionRenderKey,
    getLastSubscriptionRenderKey,
    setLastSubscriptionRenderKey,
    renderSubscriptionTasks,
    renderSubscriptionLogs,
    applySubscriptionLogs,
    applySubscriptionLogMeta,
} = {}) {
    if (!data) return;
    const currentSubscriptionState = typeof getSubscriptionState === 'function' ? (getSubscriptionState() || {}) : {};
    const nextTasks = Array.isArray(data.tasks)
        ? data.tasks
        : mergeSubscriptionTaskUpdates(currentSubscriptionState.tasks || [], data.task_updates || []);
    const nextState = {
        ...currentSubscriptionState,
        ...data,
        tasks: nextTasks,
        logs: Array.isArray(data.logs) ? data.logs : [],
        queued: Array.isArray(data.queued) ? data.queued : (currentSubscriptionState.queued || []),
        next_runs: data.next_runs || currentSubscriptionState.next_runs || {},
        summary: data.summary || currentSubscriptionState.summary || { step: '空闲', detail: '等待订阅任务' }
    };
    if (typeof setSubscriptionState === 'function') setSubscriptionState(nextState);

    const expandedMap = typeof getIntroExpanded === 'function' ? getIntroExpanded() : {};
    if (typeof setIntroExpanded === 'function' && typeof pruneTaskIntroExpanded === 'function') {
        setIntroExpanded(pruneTaskIntroExpanded(expandedMap, nextState.tasks));
    }

    const stepEl = document.getElementById('subscription-summary-step');
    if (stepEl) stepEl.innerText = nextState.summary?.step || '空闲';
    const detailEl = document.getElementById('subscription-summary-detail');
    if (detailEl) detailEl.innerText = nextState.summary?.detail || '等待订阅任务';

    const renderKey = typeof buildSubscriptionRenderKey === 'function' ? buildSubscriptionRenderKey(nextState) : '';
    const lastRenderKey = typeof getLastSubscriptionRenderKey === 'function' ? String(getLastSubscriptionRenderKey() || '') : '';
    if (forceRender || renderKey !== lastRenderKey) {
        if (typeof renderSubscriptionTasks === 'function') renderSubscriptionTasks();
        if (typeof setLastSubscriptionRenderKey === 'function') setLastSubscriptionRenderKey(renderKey);
    }
    if (Array.isArray(data.logs) && typeof applySubscriptionLogs === 'function') {
        applySubscriptionLogs(data.logs);
    } else if (Array.isArray(data.logs) && typeof renderSubscriptionLogs === 'function') {
        renderSubscriptionLogs();
    }
    if (typeof applySubscriptionLogMeta === 'function') {
        applySubscriptionLogMeta(data.log_meta || { latest_seq: data.log_total || 0 });
    }
}

export async function refreshSubscriptionState({ applySubscriptionState, compact = false } = {}) {
    try {
        const endpoint = compact ? '/subscription/status?compact=1' : '/subscription/status';
        const data = await window.MediaHubApi.getJson(endpoint);
        if (typeof applySubscriptionState === 'function') applySubscriptionState(data);
    } catch (e) {}
}

export async function clearSubscriptionLogs({
    setLastSubscriptionLogSignature,
} = {}) {
    await window.MediaHubApi.postJson('/subscription/logs/clear');
    if (typeof setLastSubscriptionLogSignature === 'function') setLastSubscriptionLogSignature('');
}
