问题描述：

   续写场景：一条轨迹上传并处理完后，用户继续用同一会话，文件末尾追加新内容、再次上传。
   现状只走了一半：client 按 hash 变化会重传，server 覆盖写入、mtime 变更——到这都对。
   但 watcher 的 discover 只把“新文件名”入队，已存在文件名只更 mtime、不重新入队；
   拆分又只挑 status=discovered 的轨迹，已 done/indexed 的不会被再选中；
   file_mtime 存了却没人消费来触发重处理。
   结果：续写新增的内容不会被拆成新 atom——增量重拆能力没接上（既不报错，也没有 _seen 跳过，是状态机不复位导致的静默丢弃）。
   底子其实是现成的：拆分本就用 last_offset 增量续拆，缺的只是“文件变了就重拆”这根线。

增量重拆能力设计：

   客户端续写内容，curosr.json发生变换。
   hash不同，触发client侧上传文件。
   client侧传到server，mtime发生变更。
   watcher检测mtime变化，将此轨迹变更为updated状态；
   updated状态下，之前拆分的offset会被拉取，agent的提示词中将会出现“此轨迹上次拆分到xxx”。
   xxx代表行号, offset我们本质语义就是行号。（续写视为纯追加，行号稳定；前文平移不处理。）
   因此，agent需要根据当前文件内容，从上次拆分到的行号开始，继续拆分后续内容。
   为了节省上下文，我们在update的过程中，为taskagent喂养的上下文中，包含之前的原子的行号范围和宿主机路径，让其可以agentic地前去读取。
  此提示词的设计应当考虑到缓存，并且不应该重新为update设计一款提示词，我们应该和discoverd共享一份taskagent的提示词。
  提交原子改由 submit_atom 工具完成（提交即校验：标记行、≥续接点、严格递增），不再解析XML。

提示词：
 TaskAgent提示词:
   ```
   SYSTEMP 这部分保持不变（仅把XML输出说明换成 submit_atom 工具说明）

   ```

   ```User部分
   本轨迹元信息：
   trajid: {traj-id}
   trajpath: {}
   source_model: {}
   上次拆分到（续接点）: {resume_line}
   本轨迹已经拆分的Atom：
   ---------------
     atomid:  {atom_id}
      atom-path: {}
      atom-line: {xx}-xxx
      atom-sumary:
    ----------------
      atomid:  {atom_id}
      atom-path: {}
      atom-line: {xx}-xxx
      atom-sumary:
    -----------------
   
   用户提问历史：
   - Query 1[line 1]: xxxx
   - Query 2[line 123]: xxxx 
   - Query 3[line 333]: xxxx 


   用户(新增)轨迹如下：
   line[444:555]:
   {user_trajectory_md_with_line_offset}



   ```
   TASKAGENT TOOLS：
   readfile(path, offset, limit)       # offset=起始行号、limit=读多少行(同 claude code 语义)；path 走白名单：仅 traj + skill/atom
   grep(kw, path)                      # 同上白名单
   submit_atom(start_line, intent, summary, tags, used_skills, ux_score)
       # 提交即校验：start_line 须是带[line:N]标记的 ## User 行、≥续接点、严格大于上一条；不合法返错让其自改
       # 整条无新意图时可不提交（0 个 atom 合法，不报错）

   ```


  验收标准设计：
     发起subagent进行验证。
     subagent创建一个mock的轨迹上传上来，等待处理完成后，再次上传同名轨迹，但是后面追加一部分内容。
     通过则应该出现新的轨迹原子（行号≥续接点、不与旧原子重叠、旧原子不重复生成）。
