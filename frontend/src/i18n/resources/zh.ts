export const zhResource = {
  app: {
    title: "OpenSakura Arena",
    name: "OpenSakura Arena",
    tagline: "做日译中翻译对战的裁判。",
  },
  language: {
    switcherLabel: "Language / 语言",
    current: "中文",
    english: "英文",
    chinese: "中文",
  },
  auth: {
    login: "登录",
    logout: "退出登录",
    signedIn: "已登录",
    bootstrap: {
      loading: "正在加载登录信息…",
      refresh: "请刷新页面后重试。",
      errors: {
        publicConfig: "加载应用配置失败（{{status}}）",
        session: "加载登录会话失败（{{status}}）",
        fallback: "加载登录会话失败",
      },
    },
    errorRoute: {
      defaultMessage: "认证无法完成，请重试。",
    },
  },
  nav: {
    home: "首页",
    battle: "对战",
    leaderboard: "排行榜",
    onboarding: "资料",
    admin: "管理",
  },
  header: {
    toggleMenu: "切换菜单",
  },
  theme: {
    light: "浅色",
    dark: "深色",
    system: "跟随系统",
    toggle: "切换主题",
    switchToLight: "切换为浅色主题",
    switchToDark: "切换为深色主题",
  },
  common: {
    loading: "加载中…",
    retry: "重试",
    save: "保存",
    cancel: "取消",
    close: "关闭",
  },
  errors: {
    generic: "出错了。",
    unauthorized: "请先登录后再继续。",
    sessionExpired: "登录已过期，请重新登录。",
    boundary: {
      title: "出错了",
      unexpected: "出现意外错误",
      tryAgain: "重试",
    },
  },
  routes: {
    home: "首页",
    battle: "翻译对战",
    leaderboard: "排行榜",
    onboarding: "资料",
    admin: "管理",
    adminModels: "模型",
    adminTasks: "任务",
    adminBattlePrepopulation: "对战预生成",
    adminServiceAccounts: "服务账号",
    notFound: "页面不存在",
    authError: "认证错误",
  },
  home: {
    title: "OpenSakura Arena",
    subtitle: "对比译文，为模型分出高下。",
    startBattle: "开始对战",
    viewLeaderboard: "查看排行榜",
    taglineBadge: "开源翻译对战平台",
    heroDescription: "对日文到中文的轻小说风格翻译进行双盲对比评价。投票选出更好的译文，帮助社区评估并改进翻译模型。",
    features: {
      blindTest: {
        title: "盲测对比（A/B）",
        description: "两个模型翻译同一段原文，你在不知道身份的情况下打分。"
      },
      communityVoting: {
        title: "社区投票",
        description: "从准确性、流畅度、风格、自然度、知识、文化、语调、术语、拒答等维度投票，凝聚共识。"
      },
      rankings: {
        title: "Elo 与 BT 排名",
        description: "结合 Elo 与 Bradley-Terry（BT）模型，给出带 95% 置信区间的排名。"
      }
    },
    howItWorks: {
      title: "运作流程",
      subtitle: "从原文到社区共建的模型排名，只需四步。",
      step1: {
        title: "原文",
        description: "从精选任务库中抽取一段日文原文。"
      },
      step2: {
        title: "盲测翻译",
        description: "两个模型翻译同一段原文，身份保密。"
      },
      step3: {
        title: "投出你的一票",
        description: "阅读两份译文，选出更好的一份。"
      },
      step4: {
        title: "更新排名",
        description: "每次投票都会触发 Elo/BT 重新计算排名。"
      },
      stepLabel: "第 {{step}} 步"
    },
    stats: {
      ratingSystem: {
        label: "评分系统",
        value: "Elo + BT"
      },
      confidence: {
        label: "置信区间",
        value: "95% CI"
      },
      voting: {
        label: "投票方式",
        value: "盲测 A/B"
      },
      footer: "加入社区，一起评估日译中翻译质量。每一票都会让模型排名更精准。"
    }
  },
  footer: {
    tagline: "开源的日译中翻译模型盲测平台，由社区投票驱动。",
    navigation: "导航",
    project: "项目",
    builtWith: "由 React、FastAPI 与社区热情共同打造",
    systems: "Elo + Bradley-Terry 评分体系"
  },
  leaderboard: {
    title: "排行榜",
    empty: {
      title: "暂无评分",
      description: "开一场对战并投票，这里就会出现模型排名。",
      cta: "开始对战"
    },
    rankLabel: "第 {{rank}} 名",
    filters: {
      judgeType: {
        all: "全部投票",
        human: "人工投票",
        bot: "机器投票"
      },
      method: {
        elo: "Elo",
        bt: "Bradley-Terry"
      },
      confidence: {
        label: "95% CI",
        show: "显示 95% CI",
        hide: "隐藏 95% CI"
      }
    },
    meta: {
      method: "方法：",
      bootstrapRounds: "（{{count}} 次 bootstrap 采样）",
      votes: {
        total: "投票：",
        totalCount: "共 <1>{{total}}</1> 票",
        breakdown: "（人工 <1>{{human}}</1>，机器 <2>{{bot}}</2>）"
      }
    },
    table: {
      rank: "排名",
      model: "模型",
      rating: "评分",
      confidence: "95% CI",
      games: "对战场数"
    },
    status: {
      unrated: "未评级",
      new: "新"
    },
    podium: {
      rank: "第 {{rank}} 名",
      rating: "评分",
      games: "{{count}} 场"
    },
    error: "加载排行榜失败"
  },
  battle: {
    title: "翻译对战",
    sourceText: "原文",
    chooseWinner: "选出更好的译文",
    submitVote: "提交投票",
    modelOutput: "模型 {{index}} 的译文",
    comparisonAriaLabel: "对战对比",
    sourcePanelTitle: "原文",
    modelA: "模型 A",
    modelB: "模型 B",
    sessionExpiredTitle: "登录已过期",
    sessionExpiredBody: "登录已过期，请重新登录。",
    voteHeader: "投出你的一票",
    votePrompt: "哪个翻译更好？",
    voteRecorded: "投票已提交。",
    voteA: "模型 A 更好",
    voteTie: "不分胜负",
    voteB: "模型 B 更好",
    rubricPrompt: "为什么这样选？（可选标签）",
    feedbackLabel: "其他反馈（可选）",
    feedbackPlaceholder: "是什么影响了你的判断？",
    submitVoteButton: "提交投票",
    updateVoteButton: "更新投票",
    submittingVoteButton: "提交中…",
    retryBattle: "重新对战",
    startAnother: "再来一场",
    revealTitle: "模型揭晓",
    revealDescription: "感谢投票！下面是每份译文背后的模型。",
    revealWinnerBadge: "胜者",
    revealTie: "你判定为平局",
    statusThinking: "思考中…",
    statusStreaming: "正在输出",
    statusWaiting: "等待输出…",
    statusComplete: "已完成",
    statusReconnecting: "正在重新连接…",
    statusFailed: "失败",
    statusError: "错误",
    statusLoading: "加载中…",
    prepopulationPreparing: "正在准备池中对战…",
    adminRevealButton: "揭晓模型",
    adminRevealAria: "揭晓 {{title}} 的身份",
    rubric: {
      accuracy: "准确性",
      fluency: "流畅度",
      style: "风格",
      consistency: "一致性",
      naturalness: "自然度",
      knowledge: "知识",
      cultural: "文化",
      voice: "语调",
      terminology: "术语",
      refusal: "拒答"
    },
    rubricDescriptions: {
      accuracy: "忠实于原文，无漏译或误译。",
      fluency: "目标语言表达流畅易读。",
      style: "语气、语域及文学风格恰当。",
      consistency: "前后术语及角色语气保持一致。",
      naturalness: "表达地道，宛如母语者所写。",
      knowledge: "正确理解特定领域的概念或背景知识。",
      cultural: "妥善处理文化背景及细微差别。",
      voice: "保留角色的个性及说话特征。",
      terminology: "准确翻译专有名词及特定术语。",
      refusal: "拒绝翻译或提供回答。"
    },
    errorEmptyStateTitle: "对战加载失败",
    returnHome: "返回首页",
    errors: {
      loginRequiredToStart: "开始对战需要先登录。",
      loginRequiredToView: "查看对战需要先登录。",
      loginRequiredToSubmit: "投票需要先登录。",
      loginRequiredToRetry: "重试需要先登录。",
      permissionDenied: "权限不足，你只能查看自己发起的对战。",
      failedToLoad: "加载对战失败",
      streamFailed: "对战数据流接收失败",
      streamEndedEarly: "对战数据流在完成前意外中断",
      sessionExpiredReload: "登录已过期或失败，请刷新页面。",
      failedToSubmitVote: "提交投票失败",
      failedToRetry: "重试对战失败",
      invalidBattleId: "无效的对战 ID",
      battleFailedPrefix: "对战失败：",
      battleFailedFallback: "对战未能完成",
      battleErrorPrefix: "对战错误：",
      runErrorPrefixA: "运行错误（A）：",
      runErrorPrefixB: "运行错误（B）：",
      runErrorPrefixGen: "运行错误：",
      runErrorFallbackA: "翻译 A 出错",
      runErrorFallbackB: "翻译 B 出错",
      runErrorFallbackGen: "翻译过程出错",
      invalidBattleResponse: "对战响应格式无效",
      invalidVoteResponse: "投票响应格式无效"
    },
  },
  onboarding: {
    title: "个人资料",
    description: "填写一些你的语言背景信息，方便后续的离线筛选与分析。",
    authNotice: {
      expiredTitle: "登录已过期",
      expiredBody: "加载或保存资料前登录已过期。请重新登录后才能保存资料、创建对战、查看对战和投票。未登录时仍可浏览排行榜。",
      requiredTitle: "保存资料需要登录",
      requiredBody: "浏览排行榜无需登录，但创建对战、查看对战和投票都需要登录。资料仅为已登录用户保存。"
    },
    checkingLogin: "正在检查登录状态…",
    identity: {
      title: "身份",
      displayName: "昵称（可选）",
      displayNamePlaceholder: "例如：N1 译者"
    },
    languagePrefs: {
      title: "语言偏好",
      uiLanguage: "界面语言",
      zhVariant: "中文变体",
      uiLangOptions: {
        en: "英文",
        zh: "中文",
        ja: "日文"
      },
      zhVariantOptions: {
        "zh-Hans": "简体（zh-Hans）",
        "zh-Hant": "繁体（zh-Hant）",
        unknown: "未知"
      }
    },
    experience: {
      title: "经验",
      jlpt: "日语水平（自评）",
      years: "日译中经验（年数）",
      roles: "担任角色",
      roleOptions: {
        translator: "翻译",
        editor: "校对",
        qc: "质检",
        tl: "组长"
      }
    },
    consent: {
      title: "授权",
      research: "允许将我的资料用于离线筛选与研究。"
    },
    save: {
      button: "保存资料",
      saving: "保存中…",
      loading: "加载中…",
      success: "保存成功",
      loadError: "加载资料失败",
      saveError: "保存资料失败"
    }
  },
  admin: {
    title: "管理",
    models: "模型",
    tasks: "任务",
    layout: {
      title: "管理",
      tabs: {
        models: "模型",
        tasks: "任务",
        battlePrepopulation: "对战预生成",
        serviceAccounts: "服务账号"
      },
      guards: {
        notAuthorized: "你没有访问管理后台的权限。",
        sessionExpired: "登录已过期，请重新登录。"
      }
    },
    tasksRoute: {
      title: "任务与任务集",
      confirmDeleteTaskSet: "确定删除该任务集吗？（须为空才能删除）",
      confirmDeleteTask: "确定删除该任务吗？",
      createSet: {
        title: "创建任务集"
      },
      editSet: {
        title: "编辑所选任务集"
      },
      createTask: {
        title: "创建单个任务"
      },
      editTask: {
        title: "编辑任务"
      },
      import: {
        title: "导入任务（.jsonl）",
        fileAriaLabel: "选择要导入的 JSONL 文件"
      },
      taskSets: {
        title: "任务集",
        allTasks: "全部任务"
      },
      tasks: {
        title: "任务",
        setLabel: "集：{{id}}",
        headers: {
          id: "ID",
          lang: "语言",
          text: "文本",
          actions: "操作"
        }
      },
      form: {
        name: "名称",
        namePlaceholder: "例如：public_jp_ln_samples",
        description: "描述",
        optionalPlaceholder: "可选",
        metadataOptional: "元数据（JSON 对象，可选）",
        metadata: "元数据（JSON 对象）",
        setMetadataPlaceholder: '{"license":"public","source":"curated"}',
        taskMetadataPlaceholder: '{"work":"...","chapter":"..."}',
        sourceLang: "源语言代码",
        targetLang: "目标语言代码",
        defaultSourceLang: "默认源语言代码",
        defaultTargetLang: "默认目标语言代码",
        sourceText: "原文",
        sourceTextPlaceholder: "日文原文",
        taskSetId: "任务集 ID"
      },
      actions: {
        create: "创建",
        creating: "创建中…",
        save: "保存",
        saving: "保存中…",
        delete: "删除",
        edit: "编辑",
        close: "关闭",
        import: "导入",
        importing: "导入中…",
        showMore: "显示更多"
      },
      values: {
        none: "（无）"
      },
      status: {
        taskSet: "任务集：{{id}}",
        showingTasks: "共显示 {{count}} 个任务",
        showingTasksForSet: "显示任务集 {{taskSetId}} 下的 {{count}} 个任务",
        imported: "已从 {{filename}} 导入 {{count}} 个任务",
        showingVisible: "显示 {{visible}} / {{total}} 个任务"
      },
      errors: {
        invalidTaskSetsResponse: "任务集响应格式无效",
        invalidTasksResponse: "任务响应格式无效",
        invalidTaskSetResponse: "任务集响应格式无效",
        invalidTaskResponse: "任务响应格式无效",
        invalidImportResponse: "导入响应格式无效",
        loadTaskSetsFailed: "加载任务集失败",
        loadTasksFailed: "加载任务失败",
        createTaskSetFailed: "创建任务集失败",
        updateTaskSetFailed: "更新任务集失败",
        deleteTaskSetFailed: "删除任务集失败",
        createTaskFailed: "创建任务失败",
        updateTaskFailed: "更新任务失败",
        deleteTaskFailed: "删除任务失败",
        importTasksFailed: "导入任务失败",
        nameRequired: "name 不能为空",
        sourceTextRequired: "source_text 不能为空",
        sourceLangRequired: "source_lang 不能为空",
        targetLangRequired: "target_lang 不能为空",
        selectJsonlFirst: "请先选择 .jsonl 文件",
        invalidJsonSyntax: "JSON 语法无效",
        expectedJsonObject: "需要 JSON 对象"
      }
    },
    battlePrepopulation: {
      title: "对战预生成",
      description: "提前生成池中对战，让用户开始对战更快。",
      stats: {
        availableAdmin: "可用的管理预生成对战",
        availableRecycled: "可用的回收对战",
        availableTotal: "可用总数",
        generating: "生成中",
        failed: "失败",
        votedConsumed: "已投票并消耗",
        total: "总数",
        maxJobSize: "最大任务规模",
        latestJob: "最新任务"
      },
      form: {
        amountLabel: "生成数量",
        model1Label: "模型 1",
        model2Label: "模型 2",
        modelEmptyOption: "任意模型",
        modelSelectionHelp: "不选择模型则自动配对；选择一个模型则与任意其他模型配对；选择两个模型则固定对局。所选模型的顺序仅限制对局组合，对战双方的位置在服务端随机决定。",
        noModelConstraint: "不限制模型",
        oneModelConstraint: "限制一个模型",
        twoModelConstraint: "限制两个模型",
        submit: "预生成对战",
        submitting: "预生成中…"
      },
      errors: {
        invalidAmount: "请输入有效数量。",
        invalidModelSelection: "请选择零个、一个或两个模型。",
        failedToLoad: "加载对战预生成数据失败",
        failedToSubmit: "提交对战预生成失败"
      },
      jobs: {
        title: "近期任务",
        empty: "暂无预生成任务。",
        none: "无",
        failedCount: "{{count}} 失败",
        headers: {
          id: "ID",
          status: "状态",
          progress: "进度",
          failed: "失败"
        }
      }
    },
    modelRegistry: {
      title: "模型注册表",
      confirmDelete: "确定删除该模型吗？",
      testResult: "测试：{{status}}（{{note}}）",
      create: {
        title: "创建模型"
      },
      edit: {
        title: "编辑模型"
      },
      form: {
        displayName: "显示名称",
        displayNamePlaceholder: "例如：gpt-4o-mini（网关）",
        modelName: "模型名称",
        modelNamePlaceholder: "例如：gpt-4o-mini",
        baseUrl: "基础 URL",
        baseUrlPlaceholder: "https://gateway.example.com（或 .../v1）",
        apiKeyOptional: "API 密钥（可选）",
        apiKeyPlaceholder: "加密存储",
        temperature: "temperature",
        frequencyPenalty: "frequency_penalty",
        presencePenalty: "presence_penalty",
        optionalPlaceholder: "（可选）",
        systemPrompt: "system_prompt（留空使用默认值）",
        systemPromptPlaceholder: "你是一名专业翻译……",
        userPrompt: "user_prompt（留空使用默认值）",
        userPromptPlaceholder: "将以下内容从 {{sourceLang}} 翻译为 {{targetLang}}：\n{{sourceText}}",
        promptTokensPrefix: "可用占位符：",
        promptTokenSeparator: "、",
        promptTokenEnd: "。",
        promptTokensSuffix: " 两个提示词都留空则使用内置默认值。",
        tagsJson: "tags（JSON 对象）",
        visibility: "可见性",
        enabled: "启用",
        paramsJson: "params（JSON 对象）",
        apiKeyHelper: "后端不会返回模型的 API 密钥。",
        newApiKeyOptional: "新 API 密钥（可选）",
        newApiKeyPlaceholder: "留空则保留原值",
        clearApiKey: "清除 API 密钥"
      },
      actions: {
        create: "创建",
        creating: "创建中…",
        edit: "编辑",
        test: "测试",
        delete: "删除",
        close: "关闭",
        save: "保存",
        saving: "保存中…"
      },
      list: {
        title: "模型",
        empty: "暂无模型。",
        headers: {
          name: "名称",
          model: "模型",
          visibility: "可见性",
          enabled: "启用",
          key: "密钥",
          actions: "操作"
        }
      },
      values: {
        yes: "是",
        no: "否",
        ok: "正常",
        fail: "失败"
      },
      errors: {
        invalidModelsResponse: "模型响应格式无效",
        invalidModelResponse: "模型响应格式无效",
        invalidModelTestResponse: "模型测试响应格式无效",
        loadFailed: "加载模型失败",
        createFailed: "创建模型失败",
        saveFailed: "保存模型失败",
        deleteFailed: "删除模型失败",
        testFailed: "测试模型失败",
        displayNameRequired: "display_name 不能为空",
        modelNameRequired: "model_name 不能为空",
        baseUrlRequired: "base_url 不能为空",
        invalidNumber: "无效的数字",
        invalidJsonSyntax: "JSON 语法无效",
        expectedJsonObject: "需要 JSON 对象"
      }
    },
    serviceAccounts: {
      title: "服务账号",
      confirmRevoke: "确定要撤销该令牌吗？",
      create: {
        title: "创建服务账号"
      },
      edit: {
        title: "编辑服务账号"
      },
      form: {
        name: "名称",
        namePlaceholder: "例如：CI/CD 机器人",
        description: "描述",
        descriptionOptional: "描述（可选）",
        enabled: "启用"
      },
      list: {
        title: "账号",
        empty: "暂无服务账号。"
      },
      token: {
        createdTitle: "令牌已创建",
        createdWarning: "请立即复制，此令牌不会再次显示。",
        listTitle: "令牌",
        createTitle: "创建令牌",
        scopesLabel: "权限范围",
        expiresAtOptional: "过期时间（可选）",
        empty: "暂无令牌。",
        statusScopes: "{{status}} • 权限：{{scopes}}",
        expires: " • 过期：{{date}}"
      },
      scopes: {
        battleCreate: {
          label: "创建对战",
          description: "允许创建新的翻译对战。"
        },
        battleRead: {
          label: "读取对战",
          description: "允许读取对战状态与结果。"
        },
        battleExecute: {
          label: "执行对战",
          description: "允许后台 worker 执行对战。"
        },
        voteCreate: {
          label: "提交投票",
          description: "允许为对战提交投票。"
        }
      },
      actions: {
        create: "创建",
        creating: "创建中…",
        edit: "编辑",
        tokens: "令牌",
        hideTokens: "隐藏令牌",
        newToken: "新建令牌",
        confirmCreate: "确认创建",
        cancel: "取消",
        dismiss: "关闭",
        revoke: "撤销",
        revoking: "…",
        close: "关闭",
        save: "保存",
        saving: "保存中…"
      },
      values: {
        disabled: "已禁用"
      },
      errors: {
        loadFailed: "加载账号失败",
        createFailed: "创建失败",
        saveFailed: "保存失败",
        createTokenFailed: "创建令牌失败",
        revokeTokenFailed: "撤销令牌失败",
        nameRequired: "name 不能为空",
        scopeRequired: "至少需要选择一项权限范围",
        invalidResponseFormat: "响应格式无效",
        invalidCreateResponse: "创建响应格式无效",
        invalidUpdateResponse: "更新响应格式无效",
        invalidCreateTokenResponse: "创建令牌响应格式无效",
        invalidRevokeResponse: "撤销响应格式无效"
      }
    }
  },
} as const;
