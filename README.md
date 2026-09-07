!!!

本文件是由手工维护的，由作者在理解vibe coding所产生的内容之后再做总结，就算有所不对但是要确保跟作者理解一致。所以禁止AI修改本文件

!!!



# vidance

## 0、可视化效果

- v0：初步简陋的流程：


<video width="640" height="360" controls>
 <source src="./assets/final_moe.mp4" type="video/mp4">
 您的浏览器不支持 video 标签。
</video>

## 1、项目构思

### 0、思考
目前我的想法是借鉴市面上的agent。然后视频生成引擎我打算就是借鉴opencode这种，只不过处理的对象不是代码而是视频。

### 1、待完成
- 消息网关：借鉴hermes微信网关
- API接入：借鉴opencode等等openAI格式协议，备选minimaxH3以及Wan以及diffusion相关其他开源
- tools调用1：借鉴web_fetch，从网上爬更真实的人像图片等等以达到去除ai的效果等等的，技术参考借鉴openvideo
- tools调用2：内部审查，借鉴LSP
- tools调用3：编辑工具，比如类似抖音特效，视频配乐等等
- skill：比如生成特定风格的，比如ai漫剧的或者营销号的或者电影的或者vlog的等等的
- 多模态输入：Word、PDF等等的，我最近还跑了一些腾讯混元的3D生成模型，感觉也可以加进去（比如真实的3D图形动起来那种风格的）


### 2、已完成
- 初步拼接：但是并没有那么自然
- 模型部署：minimaxH3和qwen3.6-35B-A3B均已部署，底层支持Loop了。现在生成模型的还部署了Wan2.2和Hunyuan，生图的部署了SDXL和FLUX，然后TTS的部署了CosyVoice，然后llm



### 3、发现的问题
- minimaxH3生成速度较慢，尝试部署wan
- 音频有小问题，建议部署文本转语言以及语音转文字的东西，分别来剧本加强和人声转AI音（爬下来之后处理）

### 4、计划路线

详见docs/roadmap.md
目前是搞定了v0，可以看看效果，然后现在正在进行v1
下面的纯属胡说，一会再更新

    先搞定tools，再搞loop，
    另外可以尝试多种模型并行，比如控制退火温度来决定是完全生成还是受约束生成，受约束生成中，是基于爬下来的做微调，还是保持故事情节不变大换血

## 2、组织架构

- assets：写README的资产
- config：配置目录，存放json配置API-key以及
- core：核心引擎
- docs：说明文档
- input：输入，比如参考帧啥的
- output：输出，比如视频啥的，（很多视频文件，所以是软链接过来的）
- ui：前端
- utils：被引擎调用的工具
- voice_samples：音频样本，如果想要cosyvoice处理TTS就把你拿下来的wav放这里，当然也可以考虑支持自己爬，也是软连接
- voices：音频的README


## 3、部署应用

全部搞完再考虑docker部署或者接入消息网关或者机器人之类的



