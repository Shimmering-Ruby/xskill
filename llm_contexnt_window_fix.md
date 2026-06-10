


存在问题：
   server llm的上下文是有限的，Task、TaskCluster、EditAgent各自都有一套方法来规避；

   Task：40000 字符的窗读取原始轨迹，内含假设是窗足够涵盖原子。 4w大概相当于10K token；
        改进方式： 是否可以TaskAgent仅仅负责输出offset？
                然后其读取内容类似 SYSP + User + Ass + User2(line num) +User3(line num) +user4(line num)
                然后其通过观看user的部分来进行拆分？

    TaskCluster： ？？
    EditAgent？
